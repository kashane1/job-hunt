---
status: complete
priority: p1
issue_id: "064"
tags: [code-review, plan-review, discovery, dedupe, architecture]
dependencies: []
---

# Add a cross-source identity design before promising authority-based dedupe

## Problem Statement

The plan promises authority-based winner selection across sources, but the current runtime only dedupes by canonical posting URL and existing application URL. Without a cross-source identity design, the repo cannot actually arbitrate between different URLs that represent the same job.

This is a merge-blocking plan issue because the plan’s acceptance criteria and test scenarios currently overpromise behavior the runtime cannot support.

## Findings

- The plan says duplicate postings should prefer a higher-authority source and gives a Workable-vs-Adzuna single-lead scenario in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:385](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:385) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:574](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:574).
- Current runtime dedupe checks canonicalized posting URL and application URL, not a broader identity model, in [/Users/simons/job-hunt/src/job_hunt/discovery.py:785](/Users/simons/job-hunt/src/job_hunt/discovery.py:785) and [/Users/simons/job-hunt/src/job_hunt/discovery.py:1009](/Users/simons/job-hunt/src/job_hunt/discovery.py:1009).
- If two providers expose different URLs for the same job, `source_authority` never gets a chance to arbitrate under the current model.

## Proposed Solutions

### Option 1: Narrow the plan to exact-URL duplicates only

**Approach:** Update the plan so authority precedence only applies when providers collapse onto the same canonical URL in the initial rollout.

**Pros:**
- Honest about current capability
- Smaller implementation surface
- Removes false expectations

**Cons:**
- Defers richer cross-source dedupe

**Effort:** 30-60 minutes

**Risk:** Low

---

### Option 2: Add a cross-source identity slice before Phase 2/4 promises

**Approach:** Add a concrete design for secondary fingerprinting or matching rules, artifact impact, migration strategy, and tests before claiming cross-source authority arbitration.

**Pros:**
- Makes the plan accurate and future-ready
- Unlocks meaningful cross-source dedupe later

**Cons:**
- Substantially expands scope

**Effort:** 2-4 hours

**Risk:** Medium

## Recommended Action

Completed by keeping authority precedence limited to exact-URL duplicates and by explicitly deferring broader cross-source identity matching to a future plan.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- `/Users/simons/job-hunt/src/job_hunt/discovery.py`

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)

## Acceptance Criteria

- [ ] The plan either narrows precedence promises to exact-URL duplicates or adds a real cross-source identity design
- [ ] Acceptance criteria and test scenarios match the actual dedupe model
- [ ] Implementers are not asked to rely on `source_authority` where the runtime has no identity bridge

## Work Log

### 2026-04-22 - Dedupe Promise Narrowed To Runtime Reality

**By:** Codex

**Actions:**
- Re-reviewed the plan’s dedupe language and confirmed it now limits precedence arbitration to observations that collapse onto the same canonical URL.
- Verified the plan explicitly says different URLs that look like the same job stay separate until a future identity/fingerprint plan exists.
- Closed this todo because the plan no longer overpromises authority-based collapse the runtime cannot support.

**Learnings:**
- Exact-URL-only language is the key honesty constraint for source-authority plans in this repo right now.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Captured the final review pass’s dedupe-architecture gap
- Logged it as P1 because the current plan promises behavior the runtime cannot currently express

**Learnings:**
- Authority precedence only matters after the repo can recognize two different source URLs as the same job
