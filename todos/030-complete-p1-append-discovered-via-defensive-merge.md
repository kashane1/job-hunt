---
status: pending
priority: p1
issue_id: "030"
tags: [code-review, data-integrity, batch-3, discovery]
dependencies: []
---

# `_append_discovered_via` must handle malformed lists, missing files, and normalized lock keys

## Problem Statement

The plan's `_append_discovered_via` shown at §"Merge helpers" uses `lead.get("discovered_via", [])` and appends. This correctly handles "field missing" but fails on two other realistic cases: (a) lead file not yet flushed to disk (race with a fresh `ingest_url`), (b) field present but malformed (`dict`, `None`, or string from a buggy earlier write). Additionally, the lock key is `str(lead_path)` — two callers passing `Path("a/b")` vs `Path("./a/b")` get different locks for the same file.

Concurrent data corruption bug class.

## Findings

Three-agent convergence (Kieran Python, Data-integrity):

- `lead.get("discovered_via", [])` returns `[]` if missing — good. But if key is present with non-list value, `.append` raises `AttributeError` on dict/None, or silently corrupts a string.
- `read_json(lead_path)` raises `FileNotFoundError` if the lead file has not yet been flushed.
- Lock key `str(lead_path)` is not canonical — different Path representations of the same file produce different lock objects. Serializes nothing in that case.

Plan location: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Merge helpers (`_LEAD_WRITE_LOCKS`, `_append_discovered_via`).

## Proposed Solutions

### Option 1: Defensive shape check + lock on lead_id, not path

**Approach:**
```python
def _append_discovered_via(lead_id: str, lead_path: Path, entry: ListingEntry,
                           watchlist_company: str) -> dict:
    lock = _lock_for_lead(lead_id)  # key on stable lead_id, not path
    with lock:
        try:
            lead = read_json(lead_path)
        except FileNotFoundError:
            # Caller should have already written the lead; if not,
            # this is a real bug. Log and raise structured error.
            raise DiscoveryError(
                f"Lead file missing during provenance append: {lead_path}",
                error_code="lead_write_race",
                remediation="Re-run discover-jobs; within-run dedup should prevent this."
            )
        existing = lead.get("discovered_via")
        if not isinstance(existing, list):
            logger.warning(
                "lead %s had non-list discovered_via (%r); resetting to []",
                lead_id, type(existing).__name__
            )
            existing = []
        existing.append({...})
        lead["discovered_via"] = existing
        write_json(lead_path, lead)
        return lead
```

Add `lead_write_race` to `DISCOVERY_ERROR_CODES`.

**Pros:** Defends against all three cases. Normalized lock key.
**Cons:** Adds one new error code.
**Effort:** Small.
**Risk:** Low.

### Option 2: Narrow defense only — just shape check

**Approach:** Fix (c) malformed list only; rely on caller ordering for (a); leave lock-key bug open.

**Pros:** Minimal change.
**Cons:** (a) and lock-key bugs persist, both exploitable under normal concurrency.
**Risk:** Medium.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Merge helpers + `DISCOVERY_ERROR_CODES`
- Future `src/job_hunt/discovery.py` implementation

## Acceptance Criteria

- [ ] Plan specifies shape-check on `existing` before `.append`.
- [ ] Plan specifies behavior when lead file is missing (raise structured error OR retry with backoff, decided).
- [ ] Lock key is stable identity (lead_id) not Path string.
- [ ] New test: `test_append_discovered_via_handles_malformed_existing` — dict/None/string cases reset to `[]`.
- [ ] New test: `test_append_discovered_via_missing_file_raises_structured`.
- [ ] New test: `test_append_discovered_via_locks_on_lead_id_not_path`.
- [ ] `lead_write_race` (or equivalent) added to `DISCOVERY_ERROR_CODES`.

## Work Log

### 2026-04-16 - Discovered during post-deepen review

**By:** kieran-python-reviewer, data-integrity-guardian

**Findings:** Two agents independently flagged the three sub-cases.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Merge helpers
