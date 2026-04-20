---
title: Reference-driven cover-letter attachment for application handoffs
category: integration-issues
date: 2026-04-19
tags:
  - cover-letter
  - application-pipeline
  - generated-content
  - manual-assist
  - schema-evolution
  - pdf-export
---

# Problem

The application pipeline could generate cover letters, but `prepare_application()`
and `apply_posting()` did not carry a reliable attachment contract into
playbooks or LinkedIn manual-assist bundles. Some playbooks mentioned cover
letters, but the executor had no durable way to resolve the real asset path,
which made attachment behavior inconsistent and hard to audit.

# Root Cause

Cover-letter generation, PDF export, application planning, and browser handoff
were each present, but they were not joined by a single source of truth.
`generated-content` records already knew the markdown and PDF paths, while
`plan.json` knew execution policy, and `apply_posting()` still emitted empty
manual-assist asset fields. Without a reference-driven seam, every new consumer
risked copying path metadata or inventing its own fallback rules.

# Solution

Keep generated-content records canonical for concrete file paths and make the
application plan reference-driven:

- `prepare_application()` now writes `generated_asset_refs` and
  `cover_letter_policy` into `plan.json`
- cover letters are generated during prepare time and PDF export is attempted
  immediately, but PDF failure is non-fatal
- `apply_posting()` resolves real resume and cover-letter paths from content ids
  when building the transient handoff bundle
- manual-assist bundles now expose real `resume_path`,
  `cover_letter_pdf_path`, and `cover_letter_md_path` values when available
- attempt/report schemas gained explicit cover-letter outcome fields so the repo
  can explain whether the letter was attached, skipped, unavailable, or
  deferred

Representative code areas:

- `src/job_hunt/application.py`
- `src/job_hunt/core.py`
- `schemas/application-plan.schema.json`
- `schemas/application-attempt.schema.json`
- `schemas/application-report.schema.json`
- `playbooks/application/*.md`

# Prevention

When a generated artifact needs to flow through multiple stages, separate the
contracts:

1. Generated-content records own concrete paths and export metadata.
2. Planning artifacts own references and policy.
3. Execution bundles resolve concrete paths as late as possible.

Also ship schema readers before relying on new fields, prefer nullable/status
fields over empty-string placeholders, and keep long-lived reports reference-
driven so they do not accumulate path-heavy local metadata.

# Verification

Verified with targeted and broader unit coverage:

```bash
python3 -m unittest /Users/simons/job-hunt/tests/test_phase4_application.py /Users/simons/job-hunt/tests/test_pipeline.py
python3 -m unittest /Users/simons/job-hunt/tests/test_application.py /Users/simons/job-hunt/tests/test_generation.py /Users/simons/job-hunt/tests/test_cover_letter_lanes.py /Users/simons/job-hunt/tests/test_pdf_export.py
```
