---
status: pending
priority: p2
issue_id: "018"
tags: [code-review, agent-native, cli-contract, batch-2]
dependencies: []
---

# CLI error contract undefined — where does IngestionError/PdfExportError JSON go?

## Problem Statement

The deepening pass added `IngestionError.to_dict()` and `PdfExportError.to_dict()` with structured `error_code` fields. But no deliverable specifies:

- Does the error JSON go to stdout or stderr?
- What exit code accompanies it?
- For successful mutation commands (`ingest-url` single-URL, `export-pdf`), does stdout also carry a JSON envelope, or a bare ID/path?
- For `check-integrity`, what's the output schema?

Without a CLI contract, the structured error codes are inert at the agent boundary — agents must still string-match free-text output.

## Findings

### Gap 1: Single-URL ingest-url output contract undefined

- Batch mode (`--urls-file`) has `{successes, failures}` JSON to stdout (specified)
- Single-URL success prints "minimal confirmation" (unspecified)
- Single-URL failure: `IngestionError.to_dict()` exists but plan never says where it goes

### Gap 2: export-pdf output contract undefined

- Success: "Path confirmation" (unspecified whether JSON or bare path)
- Failure: `PdfExportError.to_dict()` exists but plan never says where it goes, no exit code specified

### Gap 3: check-integrity output schema undocumented

- Plan says check-integrity gains new buckets (`missing_source_files`, `orphaned_pdfs`, `orphaned_ats_reports`, stuck-pending ATS, stale intake)
- Plan does not specify the JSON shape: is it a flat dict? a summary with list-of-dicts per bucket? an array of issues?
- No per-bucket agent-action mapping (parallel to the `confidence` pattern on dashboard)

### Gap 4: generate-resume CLI output is human text, not JSON

Plan line ~1098 shows:
```
Generated 3 resume variants:
  - technical_depth (content_id: xxx) [ATS: passed]
  - impact_focused (content_id: yyy) [ATS: warnings — 1 warning]
  - breadth (content_id: zzz) [ATS: errors — 1 error, see report]
```

This is not machine-parseable. Agents would need to re-read content record JSON files to get structured `ats_check.status` per variant.

### Gap 5: ats_check.status state machine has no agent-action mapping

Five states (`pending`, `passed`, `warnings`, `errors`, `check_failed`) with no documented agent action per state — parallel to the `confidence` mapping on dashboard.

## Proposed Solutions

### Option 1: Uniform CLI contract + document the ats_check state machine (Recommended)

**Unify CLI contract:**

All mutation commands produce JSON on stdout:
```
Success shape:
  {"status": "ok", "content_id": "...", "path": "...", "ats_check_status": "passed"}
  {"status": "ok", "lead_id": "...", "lead_path": "...", "ingestion_method": "url_fetch_json"}

Error shape (same envelope as IngestionError.to_dict + PdfExportError.to_dict):
  {"status": "error", "error_code": "login_wall", "message": "...", "url": "...", "remediation": "..."}

Exit codes:
  0 — success
  2 — structured error (agent can parse error_code)
  1 — unexpected error (uncaught exception; free-text on stderr)
```

Human-readable output goes to stderr (doesn't pollute the stdout contract).

**Document check-integrity output shape:**
```json
{
  "checked_at": "2026-04-16T12:00:00+00:00",
  "summary": {"has_issues": true, "issue_counts": {...}},
  "orphaned_content": [{"content_id": "...", "path": "..."}],
  "missing_source_files": [{"content_id": "...", "missing_path": "..."}],
  "orphaned_pdfs": [{"content_id": "...", "pdf_path": "..."}],
  "orphaned_ats_reports": [{"content_id": "...", "report_path": "..."}],
  "stuck_pending_ats": [{"content_id": "...", "stuck_since": "..."}],
  "stale_intake_files": [{"path": "...", "age_seconds": 3900}],
  "stale_pdfs": [{"content_id": "...", "age_delta_seconds": 3600}],
  "stale_ats_checks": [{"content_id": "...", "age_delta_seconds": 7200}],
  "dangling_leads": [{"status_path": "...", "lead_id": "..."}],
  "dangling_companies": [{"lead_id": "...", "company_research_id": "..."}]
}
```

Per-bucket agent-action mapping documented in a table in the plan (parallel to the dashboard `confidence` table).

**ats_check.status agent-action mapping:**
- `pending` — in-flight or crashed; check age via `check-integrity`
- `check_failed` — retry via `ats-check --content-id`
- `errors` — block submission, user must regenerate or override
- `warnings` — advisory, can proceed
- `passed` — ship

**generate-resume JSON output:**
```json
[
  {"content_id": "...-technical_depth-...", "variant_style": "technical_depth", "ats_check": {"status": "passed"}},
  {"content_id": "...-impact_focused-...", "variant_style": "impact_focused", "ats_check": {"status": "warnings"}},
  {"content_id": "...-breadth-...", "variant_style": "breadth", "ats_check": {"status": "errors"}}
]
```

With `--format text` opt-out for human triage.

**Freeze error code enumeration in one place:**
```python
# In ingestion.py
INGESTION_ERROR_CODES: Final = frozenset({
    "login_wall", "scheme_blocked", "private_ip_blocked", "redirect_blocked",
    "rate_limited", "timeout", "not_found", "response_too_large",
    "decompression_bomb", "dns_failed", "http_error", "network_error",
    "invalid_url", "unexpected",
})
# In pdf_export.py
PDF_EXPORT_ERROR_CODES: Final = frozenset({
    "weasyprint_missing", "source_missing", "render_failed", "pdf_fetch_blocked",
})
```

And a test that `IngestionError.error_code` is always in the frozenset.

**Effort:** Small-Medium (mostly plan edits; small code additions)
**Risk:** Low

## Recommended Action

Option 1. The structured errors only earn their keep if agents can parse them. Deliverables and acceptance criteria need an explicit "CLI Error Contract" section.

## Acceptance Criteria

- [ ] Plan deliverable: "CLI Error Contract" specifying stdout JSON for all commands, stderr for human messages, exit codes 0/1/2
- [ ] `ingest-url` single-URL success emits JSON envelope to stdout
- [ ] `export-pdf` success emits JSON envelope to stdout
- [ ] `generate-resume` emits JSON array by default, `--format text` for human view
- [ ] `check-integrity` output shape documented with all ~10 buckets
- [ ] Per-bucket agent-action mapping in plan (parallel to `confidence` mapping)
- [ ] `ats_check.status` 5-state mapping documented with per-state agent action
- [ ] Error code enums frozen in `INGESTION_ERROR_CODES` / `PDF_EXPORT_ERROR_CODES` frozenset
- [ ] Test: every raised error's `error_code` is a member of the appropriate enum

## Work Log

### 2026-04-16 - Discovery

**By:** agent-native-reviewer

**Actions:**
- Traced each mutation command's output contract — found 4 unspecified
- Noted check-integrity output schema undefined
- Recommended freezing error code enums in one place with validation tests
