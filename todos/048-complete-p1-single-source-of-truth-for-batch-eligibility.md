---
status: complete
priority: p1
issue_id: "048"
tags: [code-review, architecture, policy, application]
dependencies: []
---

# Assign one authority for batch eligibility and routing metadata

## Problem Statement

The plan says `playbook_path`, `executor_backend`, `surface_policy`, and
`batch_eligible` must each come from exactly one authority, but it never
assigns a concrete owner for `batch_eligible`.

As written, this leaves a split-brain risk between board adapters, the new
surface registry, and apply orchestration, which would let batch gating drift
between `prepare_application()` and `apply_batch()`.

## Findings

- The `Single Source Of Truth` section calls out surface metadata ownership,
  but `SurfaceSpec` does not include `batch_eligible`.
- The acceptance criteria still require `batch_eligible` to come from one
  authority.
- Existing code already stores batch eligibility on target-like records, so the
  plan needs a concrete migration target rather than a general principle.
- The routing decision table also treats batch eligibility as a first-class
  behavior field, increasing the risk of duplicate ownership.

## Proposed Solutions

### Option 1: Make `batch_eligible` surface-owned

**Approach:** Add `batch_eligible` or a surface-owned predicate to the
registry-backed surface spec and remove ownership from board adapters.

**Pros:**
- Aligns with the plan's surface-centric routing model
- Keeps batch policy with the rest of runtime surface behavior

**Cons:**
- Needs explicit handling for cases where origin-board facts affect eligibility
- Requires a migration from existing target records

**Effort:** 2-4 hours

**Risk:** Medium

---

### Option 2: Make `batch_eligible` policy-evaluator-owned

**Approach:** Define batch eligibility as a pure policy evaluation output from
surface plus normalized resolution facts, rather than as stored metadata.

**Pros:**
- Avoids stale duplicated booleans
- Makes the ownership boundary explicit

**Cons:**
- Requires tighter documentation of policy inputs
- Slightly more indirect than a simple field

**Effort:** 3-5 hours

**Risk:** Medium

---

### Option 3: Keep it on the application target and simplify the refactor

**Approach:** Extend the existing target/resolution record in place and make it
the sole owner for batch eligibility, rather than spreading ownership into a
new surface registry.

**Pros:**
- Smallest migration
- Matches current code shape

**Cons:**
- Leaves more routing metadata on a broader object
- Weakens the plan's proposed surface split

**Effort:** 1-3 hours

**Risk:** Low

## Recommended Action

Resolved by making batch eligibility a surface-owned predicate in
`job_hunt.surfaces.registry`, with board/application code consuming the
registry-backed value instead of defining its own source of truth.

## Technical Details

**Affected files:**
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:247)
- [application.py](/Users/simons/job-hunt/src/job_hunt/application.py:1763)
- [boards/base.py](/Users/simons/job-hunt/src/job_hunt/boards/base.py:11)

**Related components:**
- `prepare_application()`
- `apply_batch()`
- Board adapter resolution
- Surface registry design

**Database changes (if any):**
- Migration needed? No

## Resources

- **Plan:** [single source of truth section](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:247)
- **Plan:** [acceptance criteria](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:876)

## Acceptance Criteria

- [ ] The plan names exactly one owner for `batch_eligible`
- [ ] The chosen owner is consistent with the routing decision table and acceptance criteria
- [ ] The migration path from current target metadata is explicit
- [ ] `prepare_application()` and `apply_batch()` cannot derive conflicting eligibility values

## Work Log

### 2026-04-20 - Initial Discovery

**By:** Codex

**Actions:**
- Reviewed the plan's registry and acceptance-criteria sections
- Compared the proposed ownership model against current `application.py` and
  board target metadata
- Confirmed that `batch_eligible` is called out as single-sourced without a
  concrete authority

**Learnings:**
- This is a real architectural gap, not just missing prose
- Leaving it unresolved would reintroduce the split-brain issue the plan is
  trying to avoid

### 2026-04-20 - Resolution

**By:** Codex

**Actions:**
- Added `job_hunt.surfaces.registry` with `batch_eligible(surface, target)`
- Refactored `job_hunt.boards.registry` to hydrate surface-owned metadata from the surface registry
- Simplified board adapters so they no longer own `batch_eligible`
- Added surface registry tests and passed the full unittest suite

**Learnings:**
- Hydrating surface metadata centrally preserved current behavior while removing duplicated ownership
- `apply_batch()` now consumes registry-backed batch eligibility through the resolved target

## Notes

- This finding blocks approval because it undermines one of the plan's core
  design promises.
