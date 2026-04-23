---
status: complete
priority: p2
issue_id: "065"
tags: [code-review, plan-review, schemas, application, compatibility]
dependencies: []
---

# Clean up existing application artifact/schema parity before extending Phase 3

## Problem Statement

Phase 3 assumes application schemas are a clean baseline for adding new integration fields, but current runtime artifacts already include fields and surfaces that the schemas do not fully describe.

This matters because future integration changes will stack on top of an already drifting baseline unless the plan first restores schema parity for existing `plan.json` and `status.json` output.

## Findings

- The plan proposes adding `integration_snapshot`, `execution_mode`, and `integration_decision` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:269](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:269) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:406](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:406).
- Current runtime already writes `requires_human_submit`, `handoff_context`, and `routing_snapshot` in `/Users/simons/job-hunt/src/job_hunt/application.py`.
- `application-plan.schema.json` currently lags emitted artifacts and live surfaces, including support mismatches around fields and current surfaces such as `glassdoor_easy_apply` and `indeed_external_redirect`.

## Proposed Solutions

### Option 1: Add a Phase 3a schema-parity cleanup step

**Approach:** Before any new integration fields are planned, add a prerequisite step that validates current emitted artifacts against updated schemas and compatibility tests.

**Pros:**
- Creates a trustworthy baseline
- Prevents stacking drift on top of drift

**Cons:**
- Adds one more prerequisite slice

**Effort:** 1-2 hours

**Risk:** Low

---

### Option 2: Fold schema-parity cleanup into todo `058`

**Approach:** Treat existing parity cleanup as part of the broader consumer-first contract work.

**Pros:**
- Fewer moving pieces

**Cons:**
- Easier for the baseline cleanup to get lost inside a larger task

**Effort:** 45-90 minutes

**Risk:** Medium

## Recommended Action

Completed by making application artifact/schema parity an explicit prerequisite for any future credential-gated integration plan rather than extending application schemas in this document.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- current application schemas
- current application artifact tests

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)

## Acceptance Criteria

- [ ] The plan acknowledges current schema/runtime parity gaps before extending Phase 3
- [ ] Existing application artifacts are treated as the first compatibility target
- [ ] Validation tests are described against real emitted artifacts, not just hand-maintained examples

## Work Log

### 2026-04-22 - Future Integration Baseline Clarified

**By:** Codex

**Actions:**
- Re-reviewed the plan and confirmed it no longer extends application artifacts in this slice.
- Verified the non-goals and future-integration requirements now state that current `plan.json` / `status.json` schema parity must be fixed before any later integration plan begins.
- Closed this todo because the baseline-cleanup requirement is now explicit and no longer buried behind a larger future phase.

**Learnings:**
- Calling out schema-parity cleanup as a prerequisite is enough here; it did not need another partially-designed implementation phase.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Captured the baseline schema-parity issue from the repo-structure review pass
- Logged it separately so it does not get lost inside the larger Phase 3 contract todo

**Learnings:**
- Future integration planning is safer if current plan/status artifact schemas are corrected first
