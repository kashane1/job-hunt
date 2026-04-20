---
status: complete
priority: p2
issue_id: "051"
tags: [code-review, policy, safety, architecture]
dependencies: []
---

# Preserve the human-submit invariant in the new policy and surface model

## Problem Statement

The plan discusses future surface and policy evolution, including phrases like
`automated to final review` and `future automated-final-review policy`, but it
does not explicitly restate the repo's compile-time invariant that the agent
never clicks the final Submit button.

For a multi-board architecture, that invariant needs to be carried into the
new policy evaluator and acceptance criteria so future surfaces cannot loosen
it by accident.

## Findings

- The plan correctly discusses human review and manual assist, but not as a
  compile-time invariant that all new boards and surfaces must preserve.
- The repo's AGENTS instructions are stricter than ordinary runtime policy:
  `apply_policy.auto_submit_tiers = []` is not meant to be loosened by board or
  surface configuration.
- The routing table and policy language are broad enough that future
  implementations could misread `automated to final review` as permission to
  automate the submit click.
- Relevant prior solution docs already treat human submit as the durable safety
  boundary.

## Proposed Solutions

### Option 1: Add the invariant to the plan's design rules and acceptance criteria

**Approach:** State explicitly that the policy evaluator, surface registry, and
executor selection may change review depth but can never authorize the final
submit click.

**Pros:**
- Smallest change
- Aligns the plan with current repo policy immediately

**Cons:**
- Depends on later implementation discipline

**Effort:** 30-60 minutes

**Risk:** Low

---

### Option 2: Add an explicit no-auto-submit contract to the routing model

**Approach:** Define a first-class invariant in the surface or policy contract
that all surfaces terminate at human final review / human submit.

**Pros:**
- Harder to misinterpret
- Makes policy review simpler

**Cons:**
- Slightly more contract surface area

**Effort:** 1-2 hours

**Risk:** Low

---

### Option 3: Rely on AGENTS.md only

**Approach:** Leave the plan unchanged and treat repository policy documents as
the only source of the invariant.

**Pros:**
- No plan edits needed

**Cons:**
- Too easy for future architecture work to drift
- Leaves a gap between the plan and the governing safety policy

**Effort:** 0 hours

**Risk:** High

## Recommended Action

Resolved by restating the invariant in the plan and by making
`requires_human_submit` a durable part of the handoff artifacts and bundles.

## Technical Details

**Affected files:**
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:494)
- [AGENTS.md](/Users/simons/job-hunt/AGENTS.md:1)

**Related components:**
- Policy evaluator
- Surface registry
- Executor contract
- Human review handoff

**Database changes (if any):**
- Migration needed? No

## Resources

- **Policy:** [AGENTS.md](/Users/simons/job-hunt/AGENTS.md:1)
- **Solution:** [human in the loop on submit as ToS defense](/Users/simons/job-hunt/docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md:1)

## Acceptance Criteria

- [ ] The plan explicitly states that final submit remains a human-only action
- [ ] The policy evaluator cannot loosen the no-auto-submit invariant
- [ ] Acceptance criteria or tests preserve this boundary across all future boards and surfaces
- [ ] Terminology like `automated to final review` is clarified to avoid ambiguity

## Work Log

### 2026-04-20 - Initial Discovery

**By:** Codex

**Actions:**
- Cross-checked the plan against repo safety policy and prior solution docs
- Reviewed the routing and policy sections for wording that could drift over time
- Captured the missing architectural restatement of the no-auto-submit rule

**Learnings:**
- This is primarily a policy-contract gap in the plan, not evidence of current code misbehavior
- Multi-board abstractions need this invariant stated close to the routing model to stay safe

### 2026-04-20 - Resolution

**By:** Codex

**Actions:**
- Updated the plan to explicitly preserve the human-submit boundary across surfaces and executors
- Added `requires_human_submit` to plan/status artifacts and manual-assist bundles
- Kept `assert_auto_submit_invariant()` behavior intact while expanding artifact-level visibility
- Ran the full unittest suite successfully

**Learnings:**
- Making the invariant visible in both design docs and runtime artifacts lowers the chance of future policy drift

## Notes

- This should be resolved before the architecture is reused as the template for
  future boards.
