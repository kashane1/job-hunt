---
status: pending
priority: p1
issue_id: "016"
tags: [code-review, data-integrity, concurrency, batch-2]
dependencies: []
---

# Intake lifecycle has a success-then-rename-failure bug; ThreadPoolExecutor races

## Problem Statement

Two data-integrity issues in the URL ingestion path:

1. **Intake success-then-rename failure** — If `extract_lead` succeeds but `intake_path.replace(processed_path)` raises (permission, cross-FS move), the `except Exception` block moves the file to `failed/` with a misleading `.err` sidecar. Downstream audits see a false "failed" record for a successfully-persisted lead.

2. **ThreadPoolExecutor concurrent writes race** — `ingest_urls_file` uses `max_workers=5`. Two URLs resolving to the same `lead_id` (via fingerprint collision) from different threads write concurrently to `data/leads/{lead_id}.json`. `write_json` is atomic per-call but the sequence is not. Both threads then try `intake_path.replace(processed_path)` with the same destination — one succeeds, the other's file is gone, and its `except Exception` logs a spurious failure.

## Findings

### Issue 1: False failure on post-success rename

Plan lines ~808-825:
```python
try:
    lead = extract_lead(intake_path, output_dir)  # succeeds, writes lead.json
    processed_path = processed_dir / f"{lead['lead_id']}.md"
    intake_path.replace(processed_path)  # CAN FAIL here
    return lead
except Exception as exc:
    # Moves the intake file to failed/, writes .err
    # But the lead JSON is already successfully persisted!
    ...
```

### Issue 2: Same-fingerprint thread race

`extract_lead` writes to `data/leads/{lead_id}.json` using `write_json` (atomic rename). Two threads with the same `lead_id`:
- Both write their lead data → last writer wins, silent content loss
- Both try `intake_path.replace(processed_path)` → one succeeds, other raises
- The losing thread's `except Exception` → moves to `failed/` with `.err`

Result: sporadic "failed" records for leads that successfully persisted, just not with the content the failing thread expected.

### Issue 3: Intake directory race in pending/

Two workers writing to `_intake/pending/<hash>.md` where hashes differ → no collision. But if the same canonical_url appears twice in the URLs file, both workers use the same `intake_hash`. Second writer clobbers the first mid-extract.

## Proposed Solutions

### Option 1: Separate extract from rename; deduplicate batch input; fetch parallel / write serial (Recommended)

**Fix 1 — separate the phases:**
```python
def ingest_url(url: str, output_dir: Path, html_override: str | None = None) -> dict:
    # ... fetch phase ...
    intake_path.write_text(lead_md, encoding="utf-8")

    try:
        lead = extract_lead(intake_path, output_dir)
    except Exception:
        # Move to failed/ with .err — actual failure
        _move_to_failed(intake_path, url, canonical, exc)
        raise

    # At this point lead is persisted successfully. Bookkeeping rename is
    # best-effort — if it fails, log a warning but do not treat as failure.
    try:
        intake_path.replace(processed_dir / f"{lead['lead_id']}.md")
    except OSError as exc:
        _log_warning(f"Could not move intake to processed/: {exc}")
        # Intake file stays in pending/ — check-integrity will flag it as stale
    return lead
```

**Fix 2 — deduplicate batch input by canonical URL:**
```python
def ingest_urls_file(urls_file: Path, output_dir: Path, max_workers: int = 5) -> dict:
    raw_urls = _read_url_lines(urls_file)
    # Deduplicate by canonical form BEFORE dispatch
    seen_canonical = set()
    unique_urls = []
    for url in raw_urls:
        canonical = canonicalize_url(url)
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        unique_urls.append(url)
    # Now dispatch only unique URLs
    ...
```

**Fix 3 — consider fetch-parallel / write-serial:** Kieran reviewer's stronger suggestion:
```python
# Phase 1 (parallel): fetch + validate → in-memory dicts
# Phase 2 (serial, main thread): write intake + call extract_lead + rename
```
This gives ~80% of the speedup (network is the bottleneck) with zero threading hazards on disk writes. Only adopt if integration testing shows thread-race bugs in practice.

**Effort:** Small (a few lines)
**Risk:** Low

## Recommended Action

Option 1, all three fixes. Fix 1 (separate extract from rename) is the highest impact — prevents misleading audits. Fix 2 (dedupe) is trivial and closes the same-URL race. Fix 3 is optional defense-in-depth; adopt if testing reveals actual thread races.

## Acceptance Criteria

- [ ] `ingest_url` separates extraction success from rename success
- [ ] Post-success rename failure logs a warning but does not raise or move to `failed/`
- [ ] `ingest_urls_file` deduplicates input by canonical URL before dispatch
- [ ] Test: `ingest_url` with a mocked `intake_path.replace` that raises → lead is persisted, no `failed/` entry, warning logged
- [ ] Test: `ingest_urls_file` with same URL appearing twice → single lead, no spurious failure
- [ ] Test: concurrent ingest of two URLs that fingerprint-collide → one success, no spurious failure (or document that fingerprint collisions are not expected)

## Work Log

### 2026-04-16 - Discovery

**By:** data-integrity-guardian, kieran-python-reviewer

**Actions:**
- Traced the try/except block at plan lines 808-825
- Identified three distinct race conditions in concurrent ingestion
- Kieran reviewer suggested fetch-parallel / write-serial as a cleaner alternative
