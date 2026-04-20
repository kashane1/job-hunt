---
status: complete
priority: p2
issue_id: "049"
tags: [code-review, architecture, compatibility, recovery]
dependencies: []
---

# Add versioned routing inputs to the resume and recovery contract

## Problem Statement

The plan says persisted artifacts should store chosen `surface`,
`executor_backend`, lifecycle state, and checkpoints, but it does not require
capturing the resolver inputs or registry/spec version that produced those
choices.

Without that, old drafts can resume under new registry rules with no
deterministic replay or auditability.

## Findings

- The `Resume And Recovery Contract` focuses on durable outputs but not on the
  decision inputs that generated them.
- The `Executor Selection And Fallback Policy` allows runtime overrides and
  capability checks, which increases the chance of future drift across
  versions.
- The plan explicitly cares about tolerant-consumer rollout for persisted
  artifacts, so missing routing provenance is a meaningful compatibility hole.
- Existing repo patterns already emphasize durable audit trails for attempts and
  resumability.

## Proposed Solutions

### Option 1: Persist a routing snapshot

**Approach:** Add a durable routing snapshot to plan/status artifacts with the
normalized resolver inputs, selected board/surface/executor, and a schema or
registry version.

**Pros:**
- Deterministic replay and debugging
- Aligns with the repo's audit-trail expectations

**Cons:**
- Adds a small amount of extra artifact metadata
- Needs compatibility tests

**Effort:** 2-4 hours

**Risk:** Low

---

### Option 2: Version the registries and persist only version IDs

**Approach:** Persist a registry version identifier and re-resolve from stored
normalized inputs on resume.

**Pros:**
- Less artifact bloat
- Preserves a cleaner domain shape

**Cons:**
- Harder to debug than a full snapshot
- Requires disciplined version management

**Effort:** 3-5 hours

**Risk:** Medium

---

### Option 3: Treat re-resolution as intentional and document it

**Approach:** Keep artifacts minimal, but explicitly declare when drafts may be
re-resolved and what audit note must be recorded.

**Pros:**
- Minimal schema change
- Lowest implementation cost

**Cons:**
- Weakest reproducibility
- More operational ambiguity

**Effort:** 1-2 hours

**Risk:** High

## Recommended Action

Resolved by adding a versioned `routing_snapshot` plus durable handoff context
to `plan.json` and `status.json`, keeping routing decisions auditable and
resumable across future registry changes.

## Technical Details

**Affected files:**
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:524)
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:721)

**Related components:**
- `plan.json`
- `status.json`
- Surface resolver
- Executor selector
- Resume/recovery logic

**Database changes (if any):**
- Migration needed? Potentially, if persisted artifact fields are added

## Resources

- **Plan:** [resume and recovery contract](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:524)
- **Solution:** [ship tolerant consumers before strict producers](/Users/simons/job-hunt/docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md:1)

## Acceptance Criteria

- [ ] The plan defines how routing decisions remain reproducible across registry changes
- [ ] Persisted artifacts capture enough data to audit why a surface and executor were chosen
- [ ] Compatibility rollout steps for legacy drafts are explicit
- [ ] Tests cover resume behavior for old and new artifact shapes

## Work Log

### 2026-04-20 - Initial Discovery

**By:** Codex

**Actions:**
- Reviewed the resume, recovery, audit, and rollout sections of the plan
- Compared the proposed persistence contract against the repo's tolerant-consumer
  and audit-trail patterns
- Identified missing version/provenance data for deterministic resume

**Learnings:**
- A durable output-only contract is not enough once routing rules become registry-driven
- This is the kind of compatibility gap that tends to appear only after the first resume incident

### 2026-04-20 - Resolution

**By:** Codex

**Actions:**
- Added `routing_snapshot` with schema/resolver version and normalized routing inputs
- Persisted routing snapshot into both `plan.json` and `status.json`
- Added targeted tests for persisted routing and manual-assist metadata
- Ran the full unittest suite successfully

**Learnings:**
- A small routing snapshot gives useful determinism without turning artifacts into executor logs
- Storing the same routing context in plan and status keeps recovery simpler

## Notes

- This should land before producers start writing new registry-backed fields.
