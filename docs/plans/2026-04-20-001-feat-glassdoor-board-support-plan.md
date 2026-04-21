---
title: "feat: Add Glassdoor board support with manual-assist-first policy"
type: feat
status: active
date: 2026-04-20
---

# feat: Add Glassdoor board support with manual-assist-first policy

## Overview

Add `glassdoor.com` as a first-class origin board in the multi-board
application pipeline without weakening the repo's safety posture.

The recommended v1 posture is:

- support Glassdoor-origin leads and routing
- reuse existing ATS redirect playbooks when the resolved application host is
  already supported (`greenhouse_redirect`, `lever_redirect`,
  `workday_redirect`, `ashby_redirect`)
- treat Glassdoor-hosted apply flows as `manual_assist` first
- keep the global invariant that the agent never clicks the final Submit button
- avoid fetch/discovery assumptions until Glassdoor-specific policy and runtime
  behavior are modeled explicitly

This is intentionally more conservative than "just add `glassdoor.com` to the
allowlist and mirror Indeed." The repo already has the right abstractions to
support Glassdoor safely, but the first Glassdoor version should make an
explicit policy choice instead of inheriting one by accident.

## Brainstorm Summary

The main product and architecture question is not "can we parse one more
domain?" It is "what category of support should Glassdoor receive?"

Three viable lanes emerged during planning:

1. Full browser-automated Glassdoor Easy Apply up to the human submit gate.
2. Manual-assist-only for Glassdoor-hosted forms, plus automated reuse of
   external ATS redirects.
3. Manual/local intake only, with no Glassdoor-hosted browser work in v1.

Recommended choice:

- Ship lane 2 first.

Why:

- It fits the existing `origin_board` vs `surface` split.
- It preserves most of the product value for Glassdoor-origin jobs that end up
  on Greenhouse, Lever, Workday, or Ashby.
- It avoids committing to Glassdoor-hosted DOM automation before we have
  stronger evidence about policy risk, anti-bot behavior, and actual flow
  stability.
- It keeps the user-facing mental model simple: Glassdoor can be a source
  board immediately, but Glassdoor-hosted applications stay human-led in v1.

## External Constraints

### Glassdoor Terms of Use

Glassdoor's Terms of Use were revised on **September 29, 2025** and state that
users may not introduce software or automated agents to the service, or access
the service to create multiple accounts, generate automated messages, or
scrape/strip/mine data without express written permission.

Source:

- [Glassdoor Terms of Use](https://www.glassdoor.com/about/terms/)

This is the key reason the recommended v1 policy is manual-assist-first for
Glassdoor-hosted flows.

### Glassdoor application and trust context

Glassdoor's current Terms also say that when a user clicks the apply button,
the application is sent to the most appropriate employer contact Glassdoor has
on file, and Glassdoor advises users to exercise caution when applying and to
verify the validity of job offers.

Glassdoor also publishes current anti-scam guidance warning users that
Glassdoor will not contact them via SMS, WhatsApp, Skype, Signal, Telegram, or
similar services, and that legitimate Glassdoor emails come from the
`glassdoor.com` domain.

Sources:

- [Glassdoor Terms of Use](https://www.glassdoor.com/about/terms/)
- [Glassdoor impersonation scams](https://www.glassdoor.com/about/security/glassdoor-impersonation-scams/)

### Discovery/product context

Glassdoor's help center currently documents user-managed Glassdoor job alerts
and states that users can create up to 10 job alerts per day, on desktop or
mobile.

Source:

- [How to Create and Manage Job Alerts on Glassdoor](https://help.glassdoor.com/articles/en_US/Article/Job-Alerts-on-Glassdoor)

Inference:

- Glassdoor may be a useful discovery source eventually, but the repo does not
  need automated Glassdoor polling to ship the first trustworthy support lane.
  Manual intake and alert-assisted intake are enough for v1.

## Local Research Summary

### Existing patterns to reuse

- `src/job_hunt/boards/base.py` already models `ApplicationTarget` and the
  `BoardAdapter` contract.
- `src/job_hunt/boards/registry.py` already routes Indeed and LinkedIn through
  board adapters and then hydrates surface metadata from the surface registry.
- `src/job_hunt/surfaces/registry.py` is already the authority for
  `playbook_path`, `surface_policy`, `handoff_kind`, and `batch_eligible`.
- `playbooks/application/generic-application.md` is already a dispatch map
  from `plan.surface` to a per-surface playbook.
- `docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md`
  already established the pattern of supporting a board safely by separating
  origin from execution surface and modeling `manual_assist` as a first-class
  lane.
- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
  already established the repo-wide pattern that the agent prepares the action
  and the human remains the submission actor of record.

### Research decision

External research was necessary here because policy, trust, and product
behavior on third-party websites can change. The local architecture was strong
enough to shape the solution, but not strong enough to answer the Glassdoor
policy question safely on its own.

## Problem Statement

The repo currently supports:

- `indeed.com` as an allowlisted board with an automated playbook up to the
  human submit gate
- `linkedin.com` as an allowlisted board with both automated and
  manual-assist-style surfaces
- shared ATS redirect playbooks for Greenhouse, Lever, Workday, and Ashby

It does not yet support Glassdoor as:

- an `origin_board`
- a routing source for Glassdoor-hosted versus ATS-hosted flows
- a policy-classified surface with explicit `surface_policy`,
  `handoff_kind`, and batch eligibility
- a documented trust model for Glassdoor-origin applications

Without that work, adding Glassdoor would likely happen as ad hoc special
cases in ingestion, board resolution, and application orchestration. That
would weaken the multi-board architecture the repo just established.

## Proposed Solution

### Core recommendation

Add a `GlassdoorBoardAdapter` and a new Glassdoor-hosted manual-assist surface,
while reusing existing ATS redirect surfaces whenever the final application
host is already supported.

### Supported v1 scope

1. Glassdoor-origin leads from manual/local intake artifacts.
2. Glassdoor posting URLs that can be normalized into the system with
   `origin_board=glassdoor`.
3. Glassdoor-origin jobs whose final execution URL is a supported ATS host:
   `greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`,
   `hire.lever.co`, `*.myworkdayjobs.com`, or `jobs.ashbyhq.com`.
4. Glassdoor-hosted apply flows represented as a new manual-assist surface,
   tentatively `glassdoor_easy_apply_assisted`.
5. Shared lifecycle, provenance, attempt recording, and human-submit behavior
   identical to the current repo policy.

### Explicit v1 non-goals

1. No Glassdoor-hosted automated DOM interaction in v1.
2. No Glassdoor fetch-based discovery polling in v1.
3. No Glassdoor-specific anti-bot evasion work in v1.
4. No final-submit automation on any Glassdoor path.

## Technical Approach

### 1. Board adapter

Add:

- `src/job_hunt/boards/glassdoor.py`

Responsibilities:

- detect Glassdoor-origin URLs and normalized leads
- normalize manual intake metadata
- resolve the execution target from:
  - `canonical_url`
  - `application_url`
  - `posting_url`
  - `redirect_chain`
- map supported external ATS hosts to existing redirect surfaces
- map Glassdoor-hosted flows to `glassdoor_easy_apply_assisted`

Expected routing behavior:

| Origin board | Final host / signal | Resolved surface | Policy |
|---|---|---|---|
| `glassdoor` | `glassdoor.com` | `glassdoor_easy_apply_assisted` | `automation_forbidden_on_origin` |
| `glassdoor` | `boards.greenhouse.io` or `job-boards.greenhouse.io` | `greenhouse_redirect` | `browser_automated_human_submit` |
| `glassdoor` | `jobs.lever.co` or `hire.lever.co` | `lever_redirect` | `browser_automated_human_submit` |
| `glassdoor` | `*.myworkdayjobs.com` | `workday_redirect` | `browser_automated_human_submit` |
| `glassdoor` | `jobs.ashbyhq.com` | `ashby_redirect` | `browser_automated_human_submit` |
| `glassdoor` | unknown redirect host | stop with `suspicious_redirect_host` or equivalent escalation |

### 2. Surface registry

Add a new `SurfaceSpec` entry in `src/job_hunt/surfaces/registry.py`:

- `surface="glassdoor_easy_apply_assisted"`
- `playbook_path="playbooks/application/glassdoor-easy-apply-assisted.md"`
- `default_executor="none"`
- `default_surface_policy="automation_forbidden_on_origin"`
- `handoff_kind="manual_assist"`
- `batch_eligible=False`

This should mirror the LinkedIn assisted surface instead of inventing a new
policy shape.

### 3. Playbook

Add:

- `playbooks/application/glassdoor-easy-apply-assisted.md`

This playbook should be intentionally narrow:

- no automated navigation, clicking, typing, upload, or DOM inspection on
  `glassdoor.com`
- prepare answer bundle, resume path, cover-letter assets, and review notes
- remind the human to complete login, MFA, CAPTCHA, identity, and profile
  gates manually
- record outcome states after the human acts

Suggested checkpoint sequence:

- `preflight_done`
- `assist_bundle_ready`
- `human_form_in_progress`
- `human_ready_to_submit`
- `human_submit_recorded`
- `confirmation_captured`

### 4. Router and orchestration updates

Update:

- `src/job_hunt/boards/registry.py`
- `playbooks/application/generic-application.md`
- application routing tests

Key rule:

- the router should not duplicate Glassdoor policy decisions in multiple
  places; the board adapter and surface registry should remain authoritative.

### 5. Ingestion and allowlist posture

Recommended v1 posture:

- do **not** add `glassdoor.com` to `config/domain-allowlist.yaml` yet
- keep Glassdoor fetch/discovery outside the login-wall carve-out until the
  product explicitly decides to support Glassdoor-hosted automation

If the repo later wants browser automation on `glassdoor.com`, that should be
its own follow-up plan with explicit policy review, playbook review, and
operator approval.

### 6. Intake shape

Support Glassdoor-origin manual intake with fields such as:

```json
{
  "origin_board": "glassdoor",
  "source": "glassdoor_manual",
  "posting_url": "https://www.glassdoor.com/job-listing/...",
  "application_url": "https://boards.greenhouse.io/...",
  "canonical_url": "https://boards.greenhouse.io/...",
  "redirect_chain": [
    "https://www.glassdoor.com/job-listing/...",
    "https://boards.greenhouse.io/..."
  ]
}
```

This lets the board adapter resolve a trustworthy execution target without
relying on live Glassdoor scraping.

## SpecFlow-Style Gap Analysis

Edge cases the implementation must handle explicitly:

1. Glassdoor lead points to Glassdoor-hosted apply and later redirects
   off-origin only after the human clicks.
   Action: keep v1 manual-assist; do not attempt late rerouting from an
   automated Glassdoor session.
2. Manual intake contains a Glassdoor posting URL but no deterministic final
   ATS URL.
   Action: route to `glassdoor_easy_apply_assisted`, not to generic fallback.
3. Manual intake contains a supported ATS `application_url` but the
   `posting_url` remains on Glassdoor.
   Action: preserve `origin_board=glassdoor` and `origin_posting_url`, while
   executing the ATS playbook on the final host.
4. Unknown redirect host or suspicious tracking redirect.
   Action: block and record the host rather than auto-following.
5. Confirmation or follow-up email references the ATS host rather than
   Glassdoor.
   Action: correlation logic should remain host-agnostic and rely on existing
   posting URL / application identifier matching where possible.

## System-Wide Impact

### Interaction graph

Glassdoor support will touch the same path LinkedIn touched:

- intake normalization
- board resolution
- target hydration via surfaces registry
- `prepare_application()`
- playbook dispatch
- attempt/status lifecycle

The important rule is that Glassdoor-specific policy should enter the system at
the board/surface layer, not as conditionals scattered through
`application.py`.

### Error propagation

New Glassdoor routing should reuse current structured failures where possible:

- `suspicious_redirect_host`
- `unknown_question`
- `session_missing`
- `session_expired`
- `prompt_injection_guard_triggered`

Avoid adding Glassdoor-only error codes unless a Glassdoor-only failure mode is
truly distinct.

### State lifecycle risks

Manual-assist flows must remain auditable, not invisible. Like the LinkedIn
assisted lane, Glassdoor-hosted flows should record:

- approval requirement
- approval obtained
- answers used and provenance
- blockers encountered
- whether the human submitted
- whether confirmation was captured

### API surface parity

If Glassdoor is added as a board, parity updates should cover:

- board adapter registry
- generic playbook router
- tests for surface metadata and batch eligibility
- manual-intake parsing paths

## Implementation Phases

### Phase 1: Routing foundation

- Add `src/job_hunt/boards/glassdoor.py`
- Register the adapter in `src/job_hunt/boards/registry.py`
- Add routing tests for Glassdoor-origin ATS redirects and Glassdoor-hosted
  assisted routing

### Phase 2: Manual-assist surface

- Add `playbooks/application/glassdoor-easy-apply-assisted.md`
- Add `glassdoor_easy_apply_assisted` to `src/job_hunt/surfaces/registry.py`
- Update `playbooks/application/generic-application.md`
- Add tests for playbook discovery, metadata, and batch exclusion

### Phase 3: Intake and reporting polish

- Normalize `glassdoor_manual` intake metadata
- Ensure `prepare_application()` persists `origin_board`, `surface_policy`,
  `handoff_kind`, and `batch_eligible` correctly
- Add reporting/integrity assertions if any manual-assist states need coverage

### Phase 4: Optional follow-up discovery plan

- Decide separately whether Glassdoor watchlist/discovery support is worth the
  policy and maintenance cost
- If yes, write a dedicated plan rather than folding discovery into this one

## Alternative Approaches Considered

### Alternative A: Make Glassdoor mirror Indeed immediately

Rejected for v1.

Why:

- Glassdoor's current Terms explicitly call out automated agents.
- The repo does not yet have Glassdoor-specific runtime evidence equivalent to
  the current Indeed lane.
- A rushed allowlist decision would be difficult to unwind once scripts and
  playbooks depend on it.

### Alternative B: Support Glassdoor only as raw generic fallback

Rejected.

Why:

- It would lose the architecture win from explicit board support.
- It would force too much policy logic into generic code and operator judgment.
- It would produce weaker audit trails than a named surface.

## Dependencies and Risks

### Dependencies

- current board-adapter registry remains the source of truth for origin-board
  routing
- current surface registry remains the source of truth for surface metadata
- current manual-assist lifecycle behavior remains reusable

### Risks

- Glassdoor-hosted product behavior may differ from assumptions in manual
  intake artifacts.
- Operators may expect full automation because Indeed and LinkedIn already
  exist; docs need to make the Glassdoor distinction explicit.
- If Glassdoor URLs should be hard-failed by ingestion in practice, the plan
  may need a small follow-up tightening pass after implementation.

## Acceptance Criteria

### Functional

- [ ] A Glassdoor-origin manual intake artifact can be normalized with
      `origin_board=glassdoor`.
- [ ] A Glassdoor-origin lead with final ATS URL on Greenhouse resolves to
      `greenhouse_redirect`.
- [ ] A Glassdoor-origin lead with final ATS URL on Lever resolves to
      `lever_redirect`.
- [ ] A Glassdoor-origin lead with final ATS URL on Workday resolves to
      `workday_redirect`.
- [ ] A Glassdoor-origin lead with final ATS URL on Ashby resolves to
      `ashby_redirect`.
- [ ] A Glassdoor-origin lead with no deterministic ATS target resolves to
      `glassdoor_easy_apply_assisted`.
- [ ] `glassdoor_easy_apply_assisted` is not batch-eligible.
- [ ] The generic application router knows about the Glassdoor assisted surface.

### Safety and policy

- [ ] No Glassdoor playbook clicks the final Submit button.
- [ ] No Glassdoor-hosted playbook automates DOM interaction on `glassdoor.com`
      in v1.
- [ ] Glassdoor policy decisions are documented in the playbook and plan.
- [ ] Unknown Glassdoor redirect hosts fail closed rather than generic-fallback
      silently.

### Tests

- [ ] Unit tests cover Glassdoor board matching and routing.
- [ ] Unit tests cover surface metadata for `glassdoor_easy_apply_assisted`.
- [ ] Unit tests cover batch exclusion for Glassdoor assisted flows.
- [ ] Unit or integration tests cover plan/playbook dispatch from a
      Glassdoor-origin draft.

## Success Metrics

- Glassdoor can be added without new board-specific branching in unrelated core
  modules.
- Glassdoor-origin ATS redirects reuse existing playbooks successfully.
- Glassdoor-hosted flows produce auditable manual-assist artifacts rather than
  ad hoc notes.
- The repo can explain the Glassdoor policy posture in one sentence:
  "Glassdoor is supported as a source board now; Glassdoor-hosted apply stays
  manual in v1."

## Sources & References

### Internal

- `src/job_hunt/boards/base.py`
- `src/job_hunt/boards/registry.py`
- `src/job_hunt/boards/linkedin.py`
- `src/job_hunt/surfaces/registry.py`
- `playbooks/application/generic-application.md`
- `playbooks/application/linkedin-easy-apply-assisted.md`
- `docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md`
- `docs/plans/2026-04-19-001-feat-linkedin-and-board-adapters-plan.md`
- `docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md`
- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`

### External

- [Glassdoor Terms of Use](https://www.glassdoor.com/about/terms/)
- [Glassdoor impersonation scams](https://www.glassdoor.com/about/security/glassdoor-impersonation-scams/)
- [How to Create and Manage Job Alerts on Glassdoor](https://help.glassdoor.com/articles/en_US/Article/Job-Alerts-on-Glassdoor)
