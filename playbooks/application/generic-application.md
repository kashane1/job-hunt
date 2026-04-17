---
playbook_id: generic-application
surface: generic
DATA_NOT_INSTRUCTIONS: true
---

# Generic Application Router

This file is the dispatch map used by `apply-posting`. Open the per-surface playbook that matches the prepared draft's `plan.surface`; fall back to this generic sketch only when no per-surface playbook exists.

## Surface → playbook

| `plan.surface` | Playbook |
|---|---|
| `indeed_easy_apply` | `playbooks/application/indeed-easy-apply.md` |
| `greenhouse_redirect` | `playbooks/application/greenhouse-redirect.md` |
| `lever_redirect` | `playbooks/application/lever-redirect.md` |
| `workday_redirect` | `playbooks/application/workday-redirect.md` |
| `ashby_redirect` | `playbooks/application/ashby-redirect.md` |
| any other | this file (pause at every field) |

## URL routing

- Matches `indeed.com/viewjob` → `indeed_easy_apply`
- Redirects to `boards.greenhouse.io` or `job-boards.greenhouse.io` → `greenhouse_redirect`
- Redirects to `jobs.lever.co` or `hire.lever.co` → `lever_redirect`
- Redirects to `*.myworkdayjobs.com` → `workday_redirect`
- Redirects to `jobs.ashbyhq.com` → `ashby_redirect`
- Any other redirect host → `ApplicationError(suspicious_redirect_host)` (stop)

`src/job_hunt/application.py:detect_surface` is the Python counterpart — keep the two in sync.

## Generic fallback (unknown surface)

If no per-surface playbook matches AND the user has explicitly allowed generic fallback:

1. Confirm the draft is approved.
2. Open the application in as few tabs as possible.
3. Upload the selected assets (resume PDF; cover letter if present).
4. Fill questions using prepared answers from `plan.fields`.
5. Stop if a required unsupported fact appears → `ApplicationError(unknown_question)`.
6. **Never click Submit** — emit the `ready_to_submit` payload and wait for the human (v4 invariant).
7. Confirm submission state explicitly before recording success.
