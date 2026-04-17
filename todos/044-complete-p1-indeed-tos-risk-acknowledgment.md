---
status: complete
priority: p1
issue_id: 044
tags: [code-review, security, legal, indeed-auto-apply]
dependencies: []
resolved_at: 2026-04-17
resolution: superseded-by-policy-change
---

# Indeed ToS risk — RESOLVED via human-in-loop-on-submit (v4 policy revision)

## Resolution (2026-04-17)

Superseded by a stronger policy: **the agent fills forms but never clicks Submit — the human always does.** This replaces the proposed `.tos-acknowledged` marker-file gate with a hard architectural invariant:

- `apply_policy.auto_submit_tiers = []` (compile-time enforced; runtime overrides can tighten but not loosen)
- Every per-surface playbook's Step 6 gates on human click
- New attempt state `paused_human_abort` for the case where the user opts not to submit
- Risk severity dropped High → Medium in the plan's Risk Analysis table

**Why this is stronger than a marker file:** Indeed's ToS prohibits "third-party bots." When the human is the entity clicking Submit, the tool is a form-fill assistant, not a submission bot. This is a meaningful legal distinction that a checkbox-style acknowledgment cannot provide. A marker file only documents that the user was warned; the policy change actually reduces the surface.

**Residual risk:** automated filling at volume (log-normal pacing, coffee breaks, ≤20/day cap) could still trigger Indeed's anti-bot heuristics. `docs/guides/indeed-auto-apply.md` documents this. No acknowledgment gate needed.

See plan section "v4 Policy Revision — 2026-04-17 (Human-in-the-Loop on Submit)" at `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md`.

## Original Problem Statement (archived)

## Problem Statement

Indeed's 2026 Job Seeker Guidelines explicitly prohibit "third-party bots or other automated tools to apply for jobs." Policy does not distinguish personal automation from mass scraping. The plan at `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md` specifies that `apply-preflight` must check for a `data/.tos-acknowledged` marker file and, on first run, print the ToS risk disclosure and require user-created marker.

The legal risk is real and not fully mitigable by conservative pacing. Account bans are possible. The user must consent knowingly.

## Findings

- **Enhancement Summary item 23** and **Risk Analysis table** enumerate the risk.
- `apply-preflight` CLI spec (Phase 1b stub, Phase 4 full) mentions the marker check.
- No actual disclosure text has been drafted yet.
- No `check-integrity` rule ensures the marker exists before apply-batch can run.

## Proposed Solutions

### Option 1: Ship disclosure text as part of Phase 1a (Recommended)
Write the disclosure into `docs/guides/indeed-auto-apply.md` AND have `apply-preflight` print it to stderr on first run with a prompt like:

> This tool automates job applications on Indeed.com. Indeed's Terms of Service prohibit third-party automation and may ban your account. By continuing, you acknowledge:
> - The risk of account termination with no appeal
> - That you use this tool at your own discretion
> - That no distinction in policy is made for personal vs mass use
>
> Create `data/.tos-acknowledged` with a brief note (your name + date) to proceed.

- Pros: User sees disclosure before any code runs; marker creation is a deliberate act.
- Cons: A determined user could `touch` the marker without reading.
- Effort: Small (1-2 hours).
- Risk: Low.

### Option 2: Defer disclosure to `docs/guides/indeed-auto-apply.md` only
Skip the interactive preflight disclosure; just require the marker to exist.

- Pros: Simpler.
- Cons: User could miss the guide entirely; no interactive friction.
- Risk: Medium (legal/UX risk of user claiming they didn't know).

### Option 3: Per-batch acknowledgment (stricter)
Marker file expires every N days; preflight re-prompts.

- Pros: Repeated friction keeps risk salient.
- Cons: Noisy; undermines "autonomous" framing.
- Effort: Medium.
- Risk: Low.

## Recommended Action

Option 1. Ship disclosure text with Phase 1a. The 1-2 hours of writing is high-leverage.

## Technical Details

- Files affected: `docs/guides/indeed-auto-apply.md`, `src/job_hunt/application.py` (preflight stub in Phase 1b), `data/.tos-acknowledged` (runtime marker).
- No schema changes.

## Acceptance Criteria

- [ ] `docs/guides/indeed-auto-apply.md` opens with the full ToS risk disclosure.
- [ ] `apply-preflight` on fresh install prints disclosure to stderr and exits nonzero if marker absent.
- [ ] `check-integrity` warns if marker is older than a configurable threshold (default: never expire; future option).
- [ ] Integration test: preflight on a fresh temp dir without marker → nonzero exit + disclosure on stderr.

## Work Log

- 2026-04-17: Created from technical-review pass on indeed-auto-apply plan.

## Resources

- Plan: `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md` (Enhancement Summary item 23; v3 item 14)
- Indeed ToS: https://support.indeed.com/hc/en-us/articles/360028540531
- Indeed 2026 ToS analysis: https://aimgroup.com/2026/01/08/indeed-rewrites-the-fine-print-its-new-terms-of-service-explained/
