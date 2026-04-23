---
title: Glassdoor origin routing and human-submit automation first slice
category: integration-issues
date: 2026-04-21
tags:
  - glassdoor
  - board-adapters
  - application-pipeline
  - routing
  - playbooks
  - human-submit
---

# Problem

The job application pipeline could not support a Glassdoor-origin lead end to
end. There was no Glassdoor board adapter, no `glassdoor_easy_apply` surface,
no manual-intake normalization through `extract_lead()`, and no shared contract
for Glassdoor flows that hand off to Greenhouse, Lever, Workday, or Ashby after
the initial apply step.

# Root Cause

Glassdoor behavior was missing at the actual integration seams: board detection,
shared host rerouting, surface registration, and playbook dispatch. Without
those seams, Glassdoor support would have been either dead plan text
(`normalize_manual_intake()` never called), duplicated ATS routing logic, or an
accidental widening of the global login-wall allowlist.

# Solution

Ship a narrow first slice that adds Glassdoor as an explicit origin board
without broadening fetch/discovery scope:

- Added `src/job_hunt/boards/glassdoor.py` for Glassdoor-origin detection and
  manual-intake normalization
- Added shared ATS host helpers in `src/job_hunt/boards/routing.py` so LinkedIn,
  Glassdoor, and the registry reuse one source of truth
- Wired `src/job_hunt/core.py:extract_lead()` through
  `GlassdoorBoardAdapter.normalize_manual_intake()` for `glassdoor_manual`
- Registered `glassdoor_easy_apply` in `src/job_hunt/surfaces/registry.py`
- Added `playbooks/application/glassdoor-easy-apply.md` with:
  - origin re-assertions before input/upload
  - human-only final submit
  - terminal anti-bot/login/MFA/CAPTCHA boundaries
  - late reroute checkpoint `ats_redirect_handoff`
- Updated `playbooks/application/generic-application.md` so Glassdoor-hosted
  flows and shared ATS reroutes stay aligned

Key first-slice constraints:

- `glassdoor.com` is still not added to `config/domain-allowlist.yaml`
- Glassdoor email confirmation does not advance beyond `submitted_provisional`
- Unknown redirect hosts fail closed

# Prevention

When adding a new job board, separate these decisions before writing code:

1. How does the lead enter the system?
2. Which module owns host-to-surface routing?
3. Is origin-host automation allowed, manual-assist only, or disallowed?
4. Does enabling the board change global ingestion/discovery policy?

If the board can reroute into existing ATS playbooks, centralize that mapping in
shared routing helpers and keep the board adapter focused on origin-specific
normalization and defaults.

# Verification

Verified with focused regression coverage:

```bash
python3 -m unittest /Users/simons/job-hunt/tests/test_glassdoor_intake.py /Users/simons/job-hunt/tests/test_glassdoor_pipeline.py /Users/simons/job-hunt/tests/test_surfaces.py /Users/simons/job-hunt/tests/test_playbooks.py /Users/simons/job-hunt/tests/test_phase4_application.py /Users/simons/job-hunt/tests/test_linkedin_intake.py /Users/simons/job-hunt/tests/test_linkedin_pipeline.py
```

# Related

- [Glassdoor policy exception](/Users/simons/job-hunt/docs/solutions/security-issues/glassdoor-hosted-automation-policy-exception.md:1)
- [Gate policy-sensitive board automation plans](/Users/simons/job-hunt/docs/solutions/workflow-issues/gate-policy-sensitive-board-automation-plans.md:1)
- [LinkedIn origin support via board adapters and manual-assist routing](/Users/simons/job-hunt/docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md:1)
