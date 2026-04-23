---
status: complete
priority: p2
issue_id: "061"
tags: [code-review, plan-review, discovery, provenance, compatibility]
dependencies: []
---

# Encode source precedence as a single reusable contract

## Problem Statement

The plan says source precedence should be documented once and reused by dedupe, analytics, and reports, but the proposed metadata does not yet encode that precedence as one canonical contract.

This matters because if each consumer reconstructs precedence from multiple fields, the repo can still drift into inconsistent winner selection even after adding the new provenance taxonomy.

## Findings

- Phase 2 defines `source_kind`, `source_access`, and `source_authority`, with `source_authority` only taking `system_of_record | derived` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:374](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:374).
- The precedence ladder, however, has four tiers: ATS/public company feed, government API, board search result, and aggregator in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:385](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:385).
- As written, dedupe/reporting/analytics would still need to reconstruct the precedence from several fields, which undermines the plan’s goal of centralizing the rule.

## Proposed Solutions

### Option 1: Add a canonical precedence field or comparator contract

**Approach:** Introduce a single enum-backed precedence field or an explicit comparator contract that every consumer reuses.

**Pros:**
- Makes precedence deterministic
- Keeps dedupe/reporting/analytics aligned
- Easier to test

**Cons:**
- Adds one more explicit concept to the plan

**Effort:** 1-2 hours

**Risk:** Low

---

### Option 2: Keep the current field set and document a comparator function

**Approach:** Do not add another schema field; instead, define one comparison function that derives precedence from the existing metadata.

**Pros:**
- Less schema churn
- Still centralizes behavior

**Cons:**
- Weaker artifact visibility than an explicit field
- Easier for future consumers to bypass

**Effort:** 45-90 minutes

**Risk:** Medium

## Recommended Action

Completed by making `primary_source.precedence` the only persisted precedence field and by requiring one shared comparator contract for dedupe, analytics, and reports.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- future `schemas/lead.schema.json`
- future dedupe/reporting logic

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)
- Known pattern: [/Users/simons/job-hunt/docs/solutions/workflow-issues/land-multi-board-architecture-with-registry-owned-routing.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/land-multi-board-architecture-with-registry-owned-routing.md)

## Acceptance Criteria

- [ ] The plan defines one canonical precedence contract reused by all consumers
- [ ] The precedence contract can represent all tiers in the current ladder
- [ ] The acceptance criteria/tests name precedence behavior explicitly
- [ ] The resulting design does not require downstream consumers to infer precedence ad hoc

## Work Log

### 2026-04-22 - Precedence Contract Centralized

**By:** Codex

**Actions:**
- Updated the plan to define `primary_source.precedence` as the sole persisted precedence field.
- Added a requirement for one shared comparator helper to own precedence decisions across dedupe, analytics, and reporting.
- Closed this todo because the plan no longer leaves precedence to ad hoc reconstruction from multiple fields.

**Learnings:**
- Naming the shared comparator in the plan is enough to prevent a lot of downstream “everyone derives it differently” drift.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Consolidated architecture review findings around precedence drift
- Added a todo to force a single source of truth for precedence

**Learnings:**
- The plan already has the ladder; the missing piece is a concrete reusable contract
