---
status: pending
priority: p3
issue_id: "026"
tags: [code-review, testing, pattern, batch-2]
dependencies: []
---

# Missing end-to-end pipeline test for batch 2 flow

## Problem Statement

Pattern review noted that batch 1 has `test_pipeline.py::test_end_to_end_artifacts_are_generated` covering the full flow: extract-lead → score → draft → report → summarize. Batch 2 has no equivalent for the new commands: ingest-url → score → research-company → generate-resume → ats-check → export-pdf → update-status → apps-dashboard.

## Findings

### What exists per-module

Batch 2 plan deliverables include:
- `tests/test_pdf_export.py` (unit)
- `tests/test_ingestion.py` (unit)
- `tests/test_ats_check.py` (unit + backward-compat)
- `tests/test_analytics.py` (unit)

### What's missing

No integration test that exercises the full batch-2 flow. An end-to-end test would catch:
- Interaction Graph correctness (does CLI orchestration actually match the diagram?)
- Cross-module field flow (does `ingest-url` write the fields `generate-resume` expects?)
- `check-integrity` detecting the right state after a full pipeline run
- Agent-native: a scripted agent running the full flow should get structured JSON at every step

## Proposed Solutions

### Option 1: Add test_pipeline.py::test_batch2_end_to_end (Recommended)

New test that:
1. Starts with a mocked HTTP server returning a valid Greenhouse JSON response
2. Runs `ingest-url` against the mock URL
3. Asserts lead created with `ingestion_method: url_fetch_json`, `fingerprint_version`, `canonical_url`
4. Runs `score-lead`
5. Runs `research-company` (using lead's company)
6. Runs `generate-resume` with 3 variants, verifies 3 content records written, each with `ats_check.status != "pending"` (CLI ran ATS post-generation)
7. Runs `export-pdf` on one variant, verifies `.pdf` file and `pdf_path` updated
8. Runs `build-draft --resume-variant <content-id>`, verifies `selected_resume_content_id` populated
9. Runs `update-status --stage applied`
10. Runs `apps-dashboard`, verifies JSON output with `sample_size: 1`, `confidence: insufficient_data`
11. Runs `check-integrity`, verifies no orphans

All file writes in `tempfile.TemporaryDirectory()` to avoid polluting the real repo.

**Effort:** Medium — one test file, ~200 LOC
**Risk:** Low

## Recommended Action

Option 1. End-to-end coverage catches interaction-graph drift that unit tests miss.

## Acceptance Criteria

- [ ] `tests/test_pipeline.py::test_batch2_end_to_end` exists
- [ ] Test uses only local fixtures (HTTP server mock via `http.server`)
- [ ] Test validates every artifact against its schema
- [ ] Test verifies `check-integrity` returns clean report at the end
- [ ] Test exercises the `--resume-variant` flag on `build-draft`
- [ ] Test added to Phase 4 or Phase 5 deliverables

## Work Log

### 2026-04-16 - Discovery

**By:** pattern-recognition-specialist

**Actions:**
- Batch 1 has end-to-end test in test_pipeline.py
- Batch 2 has only per-module unit tests per deliverables
- Integration test would catch Interaction Graph drift
