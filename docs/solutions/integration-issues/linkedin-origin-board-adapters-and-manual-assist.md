---
title: LinkedIn origin support via board adapters and manual-assist routing
category: integration-issues
date: 2026-04-19
tags:
  - linkedin
  - board-adapters
  - application-pipeline
  - manual-assist
  - schemas
---

# Problem

The application pipeline treated `surface` as both the origin board and the execution target, which made LinkedIn support unsafe. LinkedIn-origin jobs needed to enter the system without reopening fetch/discovery or browser automation on `linkedin.com`, while still reusing existing ATS playbooks for Greenhouse, Lever, Workday, and Ashby redirects.

# Root Cause

Board-specific routing logic was embedded directly in `detect_surface()` and `prepare_application()`, so the system had no durable way to distinguish `origin_board=linkedin` from an execution surface like `greenhouse_redirect`. The lifecycle model also lacked a first-class human-handoff state, so LinkedIn-hosted flows would have looked like failures or untouched drafts instead of auditable manual-assist work.

# Solution

We extracted a board-adapter seam and pushed routing decisions through it:

- Added board adapters in `src/job_hunt/boards/`
- Kept existing ATS execution surfaces and playbooks
- Added a LinkedIn-only manual-assist surface: `linkedin_easy_apply_assisted`
- Extended schemas/runtime consumers before emitting LinkedIn-specific states

Key behaviors:

- LinkedIn-origin local/manual intake is normalized in `extract_lead()`
- LinkedIn-origin redirects to supported ATS hosts resolve directly to existing ATS playbooks
- LinkedIn-hosted flows emit `handoff_kind=manual_assist`
- `apply_batch --source linkedin` excludes manual-assist drafts and only selects ATS-eligible redirects
- Attempt/status lifecycle now supports `paused_manual_assist` and `awaiting_human_action`

Representative code areas:

- `src/job_hunt/boards/registry.py`
- `src/job_hunt/boards/linkedin.py`
- `src/job_hunt/application.py`
- `playbooks/application/linkedin-easy-apply-assisted.md`

# Prevention

When adding a new job board, separate these decisions up front:

1. Which board originated the lead?
2. Which surface actually executes the application?
3. Is automation allowed on that origin at all?

Ship tolerant consumers for new states and schema members before enabling producers to write them. For policy-sensitive surfaces, model manual handoff as a real lifecycle state instead of overloading `drafted` or `failed`.

# Verification

Verified with targeted and broader unit coverage:

```bash
python3 -m unittest /Users/simons/job-hunt/tests/test_application.py /Users/simons/job-hunt/tests/test_phase4_application.py /Users/simons/job-hunt/tests/test_playbooks.py /Users/simons/job-hunt/tests/test_phase7_batch.py /Users/simons/job-hunt/tests/test_linkedin_intake.py /Users/simons/job-hunt/tests/test_tracking.py /Users/simons/job-hunt/tests/test_pipeline.py /Users/simons/job-hunt/tests/test_integrity_and_compat.py
```
