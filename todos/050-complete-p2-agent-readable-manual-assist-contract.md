---
status: complete
priority: p2
issue_id: "050"
tags: [code-review, architecture, agent-native, workflow]
dependencies: []
---

# Make manual-assist and escalation flows agent-readable first-class contracts

## Problem Statement

The plan defines policy outcomes such as `manual_assist`, `skip`, and
`escalate`, but it keeps the surface layer mostly metadata-first and does not
explicitly require agent-readable checklists, resume hooks, or parity coverage
for existing recovery commands.

That creates a risk that assisted flows become implicit policy branches instead
of durable, inspectable workflows an agent can continue safely after restart.

## Findings

- The plan's `SurfaceSpec` stays intentionally lightweight while policy
  outcomes grow richer.
- The `Observability And Audit` section recommends recording useful data, but
  does not promote those fields into a mandatory durable contract.
- The acceptance criteria and testing strategy do not explicitly preserve
  agent-invocable mutation and recovery flows such as `apply_status`,
  `checkpoint_update`, `refresh_application`, `mark_applied_externally`,
  `withdraw_application`, and `reopen_application`.
- Existing repo policy treats human handoff as a first-class operational state,
  so these flows need concrete artifacts rather than informal notes.

## Proposed Solutions

### Option 1: Extend the surface contract with assisted-flow semantics

**Approach:** Add a small agent-readable contract for assisted surfaces that
names required checklist items, human-handoff checkpoints, and resume behavior.

**Pros:**
- Preserves tool parity cleanly
- Keeps manual-assist flows inspectable and testable

**Cons:**
- Slightly richer surface model
- Requires a few new tests and artifact fields

**Effort:** 3-5 hours

**Risk:** Medium

---

### Option 2: Keep surfaces simple and formalize the contract in status artifacts

**Approach:** Leave surface metadata minimal, but require `plan.json` /
`status.json` to carry structured manual-assist steps, current handoff
checkpoint, and resume instructions.

**Pros:**
- Keeps registry objects small
- Makes runtime state very explicit

**Cons:**
- Splits semantics between surface and status layers
- More artifact-shape work

**Effort:** 3-5 hours

**Risk:** Medium

---

### Option 3: Cover the gap with acceptance criteria and tests only

**Approach:** Keep the design mostly unchanged, but add explicit acceptance
criteria and regression tests for assisted flows and recovery commands.

**Pros:**
- Minimal design change
- Fastest to land

**Cons:**
- Leaves some semantics implicit
- Easier for later refactors to erode the contract again

**Effort:** 1-3 hours

**Risk:** Medium

## Recommended Action

Resolved by persisting structured handoff context for manual-assist flows and
surfacing that same contract through `apply_posting`, so assisted flows remain
agent-readable after restart.

## Technical Details

**Affected files:**
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:347)
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:505)
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:838)

**Related components:**
- Manual-assist surfaces
- Pause/resume artifacts
- Recovery and mutation CLIs
- Agent-accessible workflow state

**Database changes (if any):**
- Migration needed? Possibly, if new durable workflow fields are added

## Resources

- **Plan:** [surface spec section](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:347)
- **Plan:** [testing strategy](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:838)

## Acceptance Criteria

- [ ] Manual-assist, skip, and escalation outcomes have an explicit durable contract
- [ ] The plan states where human-handoff checkpoints and resume instructions live
- [ ] Existing mutation and recovery commands remain agent-invocable after the refactor
- [ ] Tests or acceptance criteria cover assisted-flow parity explicitly

## Work Log

### 2026-04-20 - Initial Discovery

**By:** Codex

**Actions:**
- Reviewed the plan from the `agent-native-reviewer` perspective
- Compared assisted-flow design sections against existing repo expectations for
  human handoff and resumability
- Identified the missing durable contract for agent-readable recovery state

**Learnings:**
- Metadata-only surface specs are fine for simple routing, but not sufficient
  once manual-assist and escalation semantics become first-class outcomes
- This is mostly a contract-definition gap, not necessarily a call for heavy objects

### 2026-04-20 - Resolution

**By:** Codex

**Actions:**
- Added `_handoff_context()` in `application.py`
- Persisted manual-assist checklist, review items, checkpoint, and resume instructions in artifacts
- Updated `apply_posting()` to emit the durable handoff context directly
- Added tests covering manual-assist bundle and persisted handoff metadata

**Learnings:**
- Richer artifacts were enough; no heavy behavior object was needed
- Sharing one handoff contract between persisted state and the handoff bundle reduced drift risk

## Notes

- This issue becomes more important as soon as LinkedIn and other assisted
  surfaces are added.
