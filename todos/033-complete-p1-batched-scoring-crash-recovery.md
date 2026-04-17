---
status: pending
priority: p1
issue_id: "033"
tags: [code-review, data-integrity, batch-3, scoring]
dependencies: []
---

# Batched scoring crash recovery: re-scoring story for `status: discovered` leads

## Problem Statement

Deepening moved auto-scoring from inline (per-lead immediately after ingest) to batched (after all ingestion completes). This fixes a 100-250s perf bottleneck but introduces a new failure mode: a crash during the scoring phase leaves some leads scored, others at `status: discovered` with no `fit_assessment`.

The plan does not specify the recovery story. Two agents (data-integrity, architecture) independently flagged this.

## Findings

- `score_lead` writes back to `data/leads/{lead_id}.json` AFTER discovery completes.
- If `score_lead` crashes mid-batch (LLM outage, SIGKILL, etc.): N/M leads have `fit_assessment`, the rest don't, all have `status: discovered`.
- Re-running `discover-jobs` will dedupe the un-scored leads as `already_known` and NOT re-score them — because the current auto-score path only scores newly discovered leads, not already-existing ones.
- `apps-dashboard` and `check-integrity` tolerate missing scores but don't ALERT on them.

Result: leads can be silently marooned in `status: discovered` with no fit score, invisible to the dashboard until manually re-scored.

Plan location: §Phase 4 Deliverables "Batched scoring" + §Acceptance Criteria.

## Proposed Solutions

### Option 1: Explicit resume path — `discover-jobs` re-scores existing unscored

**Approach:** At start of the scoring phase, scan `data/leads/*.json` for leads with `status: discovered` AND no `fit_assessment` AND score them alongside newly-discovered leads. Idempotent on re-run.

**Pros:** Zero new commands. Re-running `discover-jobs` automatically heals.
**Cons:** Scoring phase now scans ALL leads, not just fresh ones (O(N) read). At 10k leads, that's 200-500ms per run.
**Effort:** Small.
**Risk:** Low.

### Option 2: New `score-unscored-leads` sweep command

**Approach:** Add `python3 scripts/job_hunt.py score-unscored-leads` as a dedicated CLI for the recovery workflow.

**Pros:** Explicit separation of concerns.
**Cons:** User must know to run it. Adds 1 more CLI command to an already-expanded surface.
**Effort:** Small.
**Risk:** Low.

### Option 3: Document-only — "re-run after crash"

**Approach:** Document the behavior; rely on user knowing to re-run. No code change.

**Pros:** Zero code.
**Cons:** Silently marooned leads is a real bug class that won't be caught by the user naturally.
**Risk:** Medium.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Phase 4 Deliverables, §Acceptance Criteria
- `src/job_hunt/discovery.py` scoring phase OR new `src/job_hunt/core.py` subcommand

## Acceptance Criteria

- [ ] Plan picks one of Option 1 / 2 / 3 explicitly.
- [ ] If Option 1: scoring phase scans for `status: discovered` AND missing `fit_assessment`.
- [ ] If Option 2: new CLI command documented in README + AGENTS.md.
- [ ] New test: `test_discover_jobs_rescues_unscored_leads_on_next_run` (if Option 1).
- [ ] `check-integrity` warns on `status: discovered` leads with no `fit_assessment` older than 1h.

## Work Log

### 2026-04-16 - Discovered during post-deepen review

**By:** data-integrity-guardian, architecture-strategist

**Findings:** Two independent agents flagged the recovery gap — one at data-integrity level, one at architecture level.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- Batch-2 pattern: two-phase crash-safe integration in `src/job_hunt/ats_check.py`
