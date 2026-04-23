---
status: complete
priority: p2
issue_id: "060"
tags: [code-review, plan-review, architecture, policy-boundaries, simplicity]
dependencies: []
---

# Simplify Phase 3 and keep origin separate from execution metadata

## Problem Statement

The plan’s Phase 3 currently carries a large future integration framework and also uses `allowed_origins` in capability metadata, which risks re-collapsing the origin-vs-surface separation the plan is trying to protect.

This matters because over-design makes the plan harder to implement safely, and `allowed_origins` creates a path for authorizing an integration based on where a lead came from instead of which execution lane is actually permitted.

## Findings

- Phase 3 adds a full dormant integration framework, vendor-specific modules, config files, capability flags, durable artifacts, and tests even though the plan says write-capable integrations are later and the first slice is modeling-only in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:402](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:402) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:632](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:632).
- The capability metadata includes `allowed_origins` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:251](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:251), which conflicts with the plan’s own separation of `origin_board`, `surface`, and `application_integration`.
- The plan also proposes several durable future-facing artifact fields before a new execution lane exists, increasing rollout burden without immediate payoff in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:262](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:262).

## Proposed Solutions

### Option 1: Shrink Phase 3 to a policy boundary and follow-up trigger

**Approach:** Replace most of Phase 3 with a short non-goal: credential-gated integrations remain unsupported unless explicitly approved by a later dedicated plan. Remove vendor module skeletons and most future artifact fields from this plan.

**Pros:**
- Makes the current plan tightly discovery-focused
- Removes dormant architecture
- Reduces implementation drift

**Cons:**
- Defers some future-proofing details

**Effort:** 45-90 minutes

**Risk:** Low

---

### Option 2: Keep Phase 3, but rename and narrow the metadata

**Approach:** Keep a read-only modeling phase, but replace `allowed_origins` with `allowed_surfaces` or `allowed_execution_hosts`, drop most artifact additions, and state that only one additive `integration_decision` object is allowed if/when evaluation happens.

**Pros:**
- Preserves intent while reducing architectural spillover
- Keeps origin/surface separation intact

**Cons:**
- Still keeps some dormant design in the plan

**Effort:** 1-2 hours

**Risk:** Medium

## Recommended Action

Completed by keeping this plan discovery-focused, deferring credential-gated integrations to a later plan, and preserving execution-surface authorization as a future requirement rather than reintroducing origin-based authorization metadata here.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`

**Related patterns:**
- origin vs surface separation
- consumer-first rollout
- no-auto-submit policy boundaries

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)
- Known pattern: [/Users/simons/job-hunt/docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md)
- Known pattern: [/Users/simons/job-hunt/docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md](/Users/simons/job-hunt/docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md)

## Acceptance Criteria

- [ ] The plan no longer uses `allowed_origins` for execution authorization
- [ ] Phase 3 is either narrowed to read-only modeling or deferred to a follow-up plan
- [ ] Future integration metadata keys reinforce execution-surface policy, not origin-board policy
- [ ] Durable artifact additions are reduced to only fields with immediate execution value

## Work Log

### 2026-04-22 - Scope And Boundary Fix Confirmed

**By:** Codex

**Actions:**
- Re-reviewed the plan after its scope reduction and verified the dormant integration framework is gone.
- Confirmed the future-integration section now carries only boundary requirements, including execution-surface-based authorization instead of origin-based authorization.
- Closed this todo because the plan no longer carries the over-designed Phase 3 metadata that caused the architecture concern.

**Learnings:**
- For this repo, keeping discovery plans small is one of the strongest ways to preserve origin/surface separation.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Synthesized simplicity and architecture review findings
- Consolidated the over-design and origin/surface concerns into one tracked item

**Learnings:**
- The discovery half of the plan is substantially more implementation-ready than the future integration half
