---
status: complete
priority: p1
issue_id: "058"
tags: [code-review, plan-review, agent-native, cli, schemas, compatibility]
dependencies: []
---

# Add consumer-first CLI and status-schema rollout for Phase 3

## Problem Statement

Phase 3 changes the `prepare-application` decision boundary and introduces new integration-aware execution modes, but the plan does not yet specify the matching CLI JSON contract changes or the status-schema rollout needed to keep those changes agent-usable and backward compatible.

This matters because the repo is JSON-first for agent workflows. If the plan adds new internal routing decisions without explicitly updating affected CLI outputs and tolerant readers first, agents and resume logic can diverge from what a human can infer from artifacts.

## Findings

- The plan inserts integration selection into `prepare-application` and introduces integration modes such as `disabled`, `read_only`, `draft_only`, and `write_gated` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:425](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:425), [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:428](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:428), and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:433](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:433).
- The Phase 3 file list names `schemas/application-plan.schema.json` but not the status schema or the CLI contract work needed for `apply-preflight`, `prepare-application`, `apply-status`, `record-attempt`, or `batch-status` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:406](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:406).
- The plan also says `status.json` will carry `execution_mode` and `integration_decision`, and that missing routing/integration fields should fail validation before execution, but it does not schedule the tolerant-consumer updates that need to land before those producers write data in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:264](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:264) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:529](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:529).

## Proposed Solutions

### Option 1: Extend the current plan with an explicit Phase 3 contract rollout

**Approach:** Add a `CLI contract updates` subsection to Phase 3, list each affected command, define the new JSON fields and error codes, add `schemas/application-status.schema.json`, and require tolerant-reader tests before any producer behavior changes.

**Pros:**
- Keeps the current plan coherent without splitting work across docs
- Preserves agent-native parity and JSON-first behavior
- Matches the repo’s consumer-first rollout pattern

**Cons:**
- Makes Phase 3 larger
- Adds more planning detail for work that may still be deferred

**Effort:** 1-2 hours

**Risk:** Medium

---

### Option 2: Shrink Phase 3 to a non-goal and defer contract design to a follow-up plan

**Approach:** Remove integration-aware producer changes from this plan and replace them with a policy statement that credential-gated integrations remain unsupported until a dedicated follow-up plan defines CLI/schema changes.

**Pros:**
- Simplifies this plan substantially
- Avoids half-specified contract work
- Reduces near-term implementation risk

**Cons:**
- Loses some future-proofing in the current document
- Requires a follow-up planning step later

**Effort:** 30-60 minutes

**Risk:** Low

---

### Option 3: Hybrid

**Approach:** Keep Phase 3, but reduce it to read-only modeling plus a short mandatory checklist for CLI/status schema updates that must exist before any implementation begins.

**Pros:**
- Preserves intent while containing scope
- Makes future implementation preconditions explicit

**Cons:**
- Still leaves a partially deferred design in this plan

**Effort:** 45-90 minutes

**Risk:** Low

## Recommended Action

Completed by narrowing the plan so credential-gated integrations remain a follow-up plan concern rather than introducing new integration-aware CLI/status contracts here.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`

**Related components:**
- `prepare-application`
- `apply-status`
- `record-attempt`
- `batch-status`
- `schemas/application-plan.schema.json`
- `schemas/application-status.schema.json`

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)
- Known pattern: [/Users/simons/job-hunt/docs/solutions/workflow-issues/land-multi-board-architecture-with-registry-owned-routing.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/land-multi-board-architecture-with-registry-owned-routing.md)
- Known pattern: [/Users/simons/job-hunt/docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md)

## Acceptance Criteria

- [ ] The plan names every CLI whose JSON contract changes in Phase 3
- [ ] The plan specifies new JSON fields and structured error-code impacts for those CLIs
- [ ] `schemas/application-status.schema.json` or equivalent consumer coverage is explicitly included
- [ ] The rollout order states that tolerant readers land before integration-aware producers
- [ ] Agent-usable tests cover the new machine-readable routing state

## Work Log

### 2026-04-22 - Plan Scope Shrunk And Closed

**By:** Codex

**Actions:**
- Re-reviewed the current plan revision and confirmed the integration-aware Phase 3 contract is no longer in scope.
- Verified the plan now treats new application CLI/status changes as explicit non-goals and defers them to a separate follow-up plan after schema-parity cleanup.
- Closed this todo because the merge-blocking contract gap was removed from the plan rather than left underspecified.

**Learnings:**
- The cleanest fix for this finding was scope control, not more dormant contract design.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Ran `ce-review` on the plan document with document-configured review agents plus agent-native and learnings review
- Synthesized repeated findings around Phase 3 contract gaps and missing status-schema rollout
- Converted the review finding into a tracked todo

**Learnings:**
- The plan is strongest on discovery expansion; the main blocking risk is underspecified contract/schema rollout for future integration-aware execution

## Notes

- This is a merge-blocking plan issue because it affects agent-native parity and compatibility safety.
