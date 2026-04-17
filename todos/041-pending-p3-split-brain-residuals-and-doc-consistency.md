---
status: pending
priority: p3
issue_id: "041"
tags: [code-review, batch-3, documentation, split-brain]
dependencies: []
---

# Split-brain residuals and doc consistency (DISCOVERY_USER_AGENT test, cursor versioning convention, partial enum, discovered_via schema shape)

## Problem Statement

Four small but real drift items remain after v2 deepening:

1. **Missing test for `DISCOVERY_USER_AGENT` single-sourcing.** Acceptance says "user-agent strings reference `DISCOVERY_USER_AGENT`"; no grep-enforced test mirrors the similar test that exists for `DISCOVERY_ERROR_CODES`.
2. **Cursor `schema_version: const 1` is a net-new versioning convention** not present in other schemas (`candidate-profile.schema.json` uses string `schema_version`, not const). Plan doesn't document this as a new convention in AGENTS.md.
3. **`last_run_status: partial` enum is reachable in schema** but the plan's code path never writes `partial` (budget-capped/truncated sources leave cursor unchanged, not `partial`). Enum-vs-code mismatch.
4. **`discovered_via` JSON schema shape not shown in the plan.** Python dataclass is precise; JSON form shown only by enum listing.

## Findings

From split-brain audit + pattern-recognition + data-integrity:

- Split-brain P1: `test_discovery_user_agent_constant_single_sourced` grep test missing.
- Pattern P2: Cursor `schema_version: const 1` introduces new convention without meta-discussion.
- Data-integrity N6: `last_run_status: partial` is reachable enum but never written.
- Split-brain P2: `discovered_via` JSON schema structure not shown.

## Proposed Solutions

### Option 1: Four targeted edits

**1.** Add to Phase 1 tests: `test_discovery_user_agent_constant_single_sourced` — greps for any string matching `job-hunt/` in discovery.py/utils.py/ingestion.py that isn't the constant definition.

**2.** Add to AGENTS.md: "Schema versioning convention. Long-lived state files (cursors, caches) use `schema_version` as an integer const starting at 1. Per-run artifacts do not require versioning. Migration is via one-shot script OR delete-and-rescan when the artifact is a rebuildable derived file."

**3.** Either (a) drop `partial` from the cursor schema enum, OR (b) write `partial` for budget-capped sources (preserving last_run_at for observability without advancing the cursor position). Pick one.

**4.** Add the JSON schema form of a `discovered_via` entry as a concrete example in the Schema Additions section of the plan.

**Pros:** Each fix is ≤10 lines of plan edit.
**Cons:** Four sub-items to track.
**Effort:** Small.
**Risk:** Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Phase 1 tests, §Schema additions
- `AGENTS.md` (post-implementation)
- `schemas/discovery-cursor.schema.json` (post-implementation)

## Acceptance Criteria

- [ ] `test_discovery_user_agent_constant_single_sourced` in Phase 1 tests.
- [ ] AGENTS.md (or plan §Notes) documents the schema_version integer-const convention.
- [ ] Cursor `last_run_status` enum matches code: either `partial` removed, or code writes `partial` for budget-capped.
- [ ] `discovered_via` JSON shape example shown in plan.

## Work Log

### 2026-04-16 - Post-deepen audit

**By:** learnings-researcher (split-brain), pattern-recognition-specialist, data-integrity-guardian

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- `docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md`
