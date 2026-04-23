# Glassdoor Auto-Apply — Operator Guide

This guide documents the narrow first slice of Glassdoor support:

- explicit Glassdoor manual/local intake is supported
- the agent may automate Glassdoor-hosted form filling up to the human submit gate
- `glassdoor.com` is **not** added to the global login-wall allowlist in this slice
- Glassdoor submissions remain `submitted_provisional` until verified confirmation support exists

## Policy posture

Glassdoor's published Terms dated September 29, 2025 prohibit introducing
software or automated agents to the service without express written
permission. This repo therefore treats Glassdoor-hosted automation as a
board-specific product-policy exception, not a generic new default.

Operational constraints:

- The agent fills fields but **never** clicks the final Submit button.
- Login, MFA, CAPTCHA, anti-bot, and account-creation boundaries remain human-handled.
- Anti-bot signals are terminal aborts. Do not retry with refresh loops or alternate tactics.

## Intake scope

Use `extract-lead` from a local JSON/markdown artifact that already contains
the Glassdoor posting metadata. Example:

```json
{
  "origin_board": "glassdoor",
  "source": "glassdoor_manual",
  "posting_url": "https://www.glassdoor.com/job-listing/example",
  "application_url": "https://www.glassdoor.com/job-listing/example",
  "redirect_chain": [
    "https://www.glassdoor.com/job-listing/example"
  ]
}
```

This first slice intentionally does not reopen generic fetch/discovery against
`glassdoor.com`.

## Routing behavior

- Glassdoor-hosted final URL → `glassdoor_easy_apply`
- Glassdoor-origin redirect to Greenhouse/Lever/Workday/Ashby → shared ATS redirect playbook
- Unknown redirect host → fail closed
- Late ATS handoff after clicking Apply → checkpoint `ats_redirect_handoff`, then re-resolve through the shared router while preserving `origin_board=glassdoor`

## Submission lifecycle

- Pre-submit: agent emits `ready_to_submit: true`
- Submit: human clicks in Chrome
- Post-submit: agent records `submitted_provisional` when a confirmation signal is visible
- Email confirmation: deferred until Glassdoor-specific sender verification lands with tests
