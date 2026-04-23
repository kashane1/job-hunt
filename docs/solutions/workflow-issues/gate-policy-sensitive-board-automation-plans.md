---
title: Gate policy-sensitive board automation plans behind explicit exceptions and provisional confirmation state
date: 2026-04-21
module: planning_and_application_architecture
problem_type: workflow_issue
component: documentation
tags:
  - planning
  - workflow
  - glassdoor
  - browser-automation
  - policy-boundaries
  - confirmation
  - allowlist
severity: high
---

# Gate policy-sensitive board automation plans behind explicit exceptions and provisional confirmation state

## Problem

The Glassdoor automation plan looked implementation-ready, but review exposed
that it still treated a policy-sensitive board integration like a normal
surface addition. The draft proposed a full `glassdoor_easy_apply` automation
lane even though the plan itself cited Glassdoor Terms language prohibiting
automated agents on the service.

## Root Cause

The draft mixed four separate decisions into one rollout:

- board support
- browser automation on the origin host
- global login-wall allowlist widening
- confirmation-state promotion

Because those were bundled together, the plan understated two critical risks:

- **policy conflict**: the human-submit invariant did not fully answer the
  quoted "no automated agents" language
- **state integrity gap**: the plan included confirmation capture while
  deferring Glassdoor-specific sender verification, which would leave drafts
  open to forged confirmation messages

## Solution

Harden the plan before implementation starts:

1. Add an explicit **policy-exception gate** before any Glassdoor-hosted
   automation work begins.
2. Keep the first slice on **manual/local Glassdoor intake** unless allowlist
   widening is explicitly approved in the same rollout.
3. End the first slice at **`submitted_provisional`** unless Glassdoor sender
   allowlist, DKIM verification, and body-correlation rules land in the same
   change.
4. Wire Glassdoor manual-intake normalization through
   `src/job_hunt/core.py:extract_lead()` so the adapter work is real, not dead
   plan text.
5. Centralize ATS rerouting in the shared routing layer so Glassdoor does not
   duplicate Greenhouse/Lever/Workday/Ashby host mapping.
6. Make anti-bot handling a **terminal abort**, not a vague "stop if needed"
   instruction.

Representative plan changes:

```markdown
## Policy Gate

This plan is only ready to execute if the repo first records an explicit
service-specific policy exception for Glassdoor-hosted automation.
```

```markdown
Recommendation:

- keep v1 at `submitted_provisional`
- promote Glassdoor email-driven confirmation only after collecting real sample
  messages and landing the verification rules with tests
```

## Prevention

When a new board integration touches a service whose policy language is
stricter than the currently supported stack:

- treat browser automation on that origin as a separate approval decision
- separate browser-lane work from global allowlist changes
- separate provisional submission from verified confirmation
- wire new adapter behavior through the actual shared entrypoint before calling
  the plan ready

One practical rule: if a plan says "follow-up work may be needed" for sender
verification or policy exceptions, the feature is usually not ready to claim a
fully closed-loop lifecycle yet.

## References

- [docs/plans/2026-04-20-002-feat-glassdoor-human-submit-automation-plan.md](../../plans/2026-04-20-002-feat-glassdoor-human-submit-automation-plan.md)
- [docs/plans/2026-04-20-001-feat-glassdoor-board-support-plan.md](../../plans/2026-04-20-001-feat-glassdoor-board-support-plan.md)
- [docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md](../security-issues/human-in-the-loop-on-submit-as-tos-defense.md)
- [docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md](./harden-board-integration-plans-with-origin-surface-separation.md)
- [docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md](./ship-tolerant-consumers-before-strict-producers.md)
