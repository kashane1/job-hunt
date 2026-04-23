---
status: complete
priority: p1
issue_id: "063"
tags: [code-review, plan-review, discovery, schemas, compatibility]
dependencies: []
---

# Make discovery provenance rollout truly consumer-first

## Problem Statement

The plan says the rollout will be consumer-first, but Phase 1 still adds new providers before the lead schema and compatibility tests are ready to represent the new `discovered_via.source` values those providers would emit.

This is a merge-blocking plan issue because the repo already persists `discovered_via.source`, and today the schema only allows a narrow fixed enum. Shipping new providers before expanding that contract would violate the repo’s compatibility-safe rollout standard.

## Findings

- The rollout sequence says readers/tests/docs land first, but Phase 1 still adds Ashby, Workable, and USAJOBS providers in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:286](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:286) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:297](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:297).
- Current discovery writes `discovered_via.source` on both duplicate and fresh-ingest paths in [/Users/simons/job-hunt/src/job_hunt/discovery.py:795](/Users/simons/job-hunt/src/job_hunt/discovery.py:795) and [/Users/simons/job-hunt/src/job_hunt/discovery.py:1075](/Users/simons/job-hunt/src/job_hunt/discovery.py:1075).
- `schemas/lead.schema.json` currently allows only `greenhouse_board`, `lever_board`, `careers_html`, `careers_html_review`, and `manual` in [/Users/simons/job-hunt/schemas/lead.schema.json:51](/Users/simons/job-hunt/schemas/lead.schema.json:51).

## Proposed Solutions

### Option 1: Move schema/compat updates ahead of provider emission

**Approach:** Explicitly land the `discovered_via.source` enum expansion, tolerant-reader coverage, and schema/compat tests before any new provider can emit new source values.

**Pros:**
- Matches the repo’s stated rollout rule
- Removes ambiguity for implementers
- Prevents schema/runtime drift

**Cons:**
- Adds an up-front prerequisite slice

**Effort:** 45-90 minutes

**Risk:** Low

---

### Option 2: Temporarily alias new providers to existing source values

**Approach:** Allow Phase 1 providers to emit an existing source enum until the schema patch lands.

**Pros:**
- Smaller initial schema change

**Cons:**
- Loses source fidelity
- Creates temporary ambiguity in provenance

**Effort:** 30-60 minutes

**Risk:** Medium

## Recommended Action

Completed by adding an explicit Phase 0 parity pass and making it a hard prerequisite before any new provider emits new discovery source tokens.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- `/Users/simons/job-hunt/schemas/lead.schema.json`
- `/Users/simons/job-hunt/src/job_hunt/discovery.py`

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)

## Acceptance Criteria

- [ ] The plan explicitly sequences lead-schema/source-enum updates before provider emission
- [ ] Compatibility tests cover `discovered_via.source` for new providers
- [ ] The rollout order no longer relies on producers writing unsupported schema values

## Work Log

### 2026-04-22 - Consumer-First Rollout Tightened

**By:** Codex

**Actions:**
- Added a dedicated Phase 0 to restore discovery-contract parity before Ashby, Workable, or USAJOBS emission begins.
- Updated the rollout sequence and Phase 1 notes so baseline schema/source drift is repaired before expansion work starts.
- Closed this todo because the plan now has a truly consumer-first rollout instead of mixing baseline cleanup into provider delivery.

**Learnings:**
- Making the baseline slice explicit is much clearer than relying on one sentence buried inside a broader provider phase.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Incorporated the final review agent’s schema-reality finding
- Created a P1 todo because the current rollout order is internally contradictory

**Learnings:**
- The new discovery providers are only safe if provenance emission is staged against the existing lead schema
