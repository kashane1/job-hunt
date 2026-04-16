---
status: pending
priority: p1
issue_id: "017"
tags: [code-review, data-integrity, batch-2]
dependencies: []
---

# --overwrite + --skip-ats-check leaves stale ats_check and stale PDF undetected

## Problem Statement

The plan introduces `--overwrite` (on generate-*) and `--skip-ats-check` flags, and `pdf_path` + `ats_check` nested fields on the content record. The interaction of these three is undocumented and produces dangling state:

- `--overwrite` replaces `{content_id}.md` and `.json`
- If regeneration runs WITHOUT running ATS check (via `--skip-ats-check` or a separate `ats-check` step that never happens), the new content record's `ats_check` field is either stale (old status from the pre-overwrite run) or absent
- PDF is regenerated on-demand; if the user overwrites the markdown but doesn't re-run `export-pdf`, `pdf_path` still points at the prior render. `pdf_generated_at` is now older than `generated_at` — a "stale PDF" state the plan never detects

## Findings

### Gap 1: Stale `ats_check` on overwrite + skip

If `--overwrite` regenerates content and the user passes `--skip-ats-check`:
- Content record's `ats_check` field is either copied forward (stale) or absent (inconsistent with the prior file's state)
- The ATS report on disk at `data/generated/ats-checks/{content-id}-check.json` either gets overwritten by the next real check OR remains pointing at stale evaluation

### Gap 2: Stale PDF after markdown regeneration

- User runs `generate-resume` with `--overwrite` → `.md` is new, `.json` is new
- `pdf_path` field either gets cleared or carries forward pointing at the old PDF
- `export-pdf` was not re-run → `.pdf` on disk is the old render
- No field comparison detects this: `generated_at` > `pdf_generated_at` is the smoking gun

### Gap 3: No check-integrity rule for either

`check-integrity` extensions cover:
- `orphaned_pdfs` (PDF exists but content record missing)
- `missing_source_files` (content record's `output_path` doesn't exist)

It does NOT cover:
- `stale_pdfs` — PDF exists, content record exists, but `pdf_generated_at < generated_at`
- `stale_ats_checks` — `ats_check.checked_at < generated_at`

## Proposed Solutions

### Option 1: Clear ats_check and pdf_path on overwrite + add staleness check (Recommended)

**On `--overwrite`:** regeneration explicitly resets the nested fields:
```python
# In core.py when building a new content record via generate-* with --overwrite
new_record["ats_check"] = {"status": "not_checked"}  # explicit reset
new_record["pdf_path"] = None  # or omit
# pdf_generated_at is not carried forward
```

**On regeneration without re-running ATS:** If `--skip-ats-check` is passed, `ats_check.status` stays as `"not_checked"`. Existing ATS report on disk is now orphaned; `check-integrity`'s `orphaned_ats_reports` scan catches it.

**Add staleness check to check-integrity:**
```python
def _detect_stale_artifacts(content_records: list[dict]) -> dict:
    stale_pdfs = []
    stale_ats = []
    for record in content_records:
        generated_at = record.get("generated_at", "")
        pdf_at = record.get("pdf_generated_at", "")
        if record.get("pdf_path") and pdf_at and pdf_at < generated_at:
            stale_pdfs.append({
                "content_id": record["content_id"],
                "generated_at": generated_at,
                "pdf_generated_at": pdf_at,
            })
        ats = record.get("ats_check", {})
        if ats.get("checked_at") and ats["checked_at"] < generated_at:
            stale_ats.append({
                "content_id": record["content_id"],
                "generated_at": generated_at,
                "ats_checked_at": ats["checked_at"],
            })
    return {"stale_pdfs": stale_pdfs, "stale_ats_checks": stale_ats}
```

Extend `check-integrity` report to include these two buckets.

**Document the interaction explicitly** in the plan's State Lifecycle Risks section:
- `--overwrite` resets `ats_check.status: "not_checked"` and clears `pdf_path`
- Re-running `generate-*` requires re-running `export-pdf` and optionally `ats-check`
- `check-integrity` surfaces stale PDFs (ISO timestamp comparison) and stale ATS checks

**Effort:** Small
**Risk:** Low

## Recommended Action

Option 1. All three fixes — reset on overwrite, add staleness detection, document in plan.

## Acceptance Criteria

- [ ] Plan's code for `generate-*` with `--overwrite` explicitly resets `ats_check` and `pdf_path`
- [ ] `check-integrity` output includes `stale_pdfs` and `stale_ats_checks` buckets
- [ ] Plan's State Lifecycle Risks section documents the interaction
- [ ] Test: `generate-resume --overwrite --skip-ats-check` produces content record with `ats_check.status == "not_checked"`
- [ ] Test: overwrite markdown then run `check-integrity` → `stale_pdfs` contains the record
- [ ] Test: regenerate content without re-running ATS → `stale_ats_checks` bucket contains the record

## Work Log

### 2026-04-16 - Discovery

**By:** data-integrity-guardian

**Actions:**
- Traced `--overwrite` interaction with the two nested fields
- Noted timestamp comparison is a trivial staleness signal
- Recommended explicit reset to avoid carry-forward bugs
