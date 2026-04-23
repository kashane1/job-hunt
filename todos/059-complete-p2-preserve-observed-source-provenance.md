---
status: complete
priority: p2
issue_id: "059"
tags: [code-review, plan-review, provenance, auditability, discovery]
dependencies: []
---

# Preserve all observed source provenance during dedupe

## Problem Statement

The plan introduces source precedence and authority metadata, but it does not yet require additive provenance retention after duplicates are merged.

This matters because strict-answer auditing depends on being able to explain both which source won and what other sources were observed. A single winning `source_*` record is not enough once dedupe collapses multiple feeds into one lead.

## Findings

- Phase 2 adds single-value fields like `source_kind`, `source_access`, `source_authority`, and `source_provider` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:367](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:367).
- The plan also defines a precedence ladder for picking a winner during dedupe in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:385](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:385).
- There is no explicit requirement to retain all observed sources after the winner is chosen, even though the review context calls for durable audit trails in `/Users/simons/job-hunt/compound-engineering.local.md`.

## Proposed Solutions

### Option 1: Add `observed_sources[]` plus `primary_source`

**Approach:** Keep a single primary source for downstream logic, but also persist an append-only `observed_sources[]` history or equivalent references to all seen provider/origin combinations.

**Pros:**
- Preserves auditability without sacrificing simple downstream reads
- Makes duplicate resolution explainable
- Supports future analytics on source overlap

**Cons:**
- Adds one more additive field family
- Requires merge/update rules during dedupe

**Effort:** 2-3 hours

**Risk:** Low

---

### Option 2: Keep current single-source fields and document that only the winner is retained

**Approach:** Explicitly state that non-winning sources are intentionally discarded.

**Pros:**
- Minimal implementation complexity

**Cons:**
- Weakens provenance
- Conflicts with trust-first audit goals

**Effort:** 15-30 minutes

**Risk:** High

## Recommended Action

Completed by requiring `primary_source` plus append-only `observed_sources[]` provenance so winner selection does not erase losing-side evidence.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- likely future `schemas/lead.schema.json`
- likely future discovery run artifacts

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)
- Review context: [/Users/simons/job-hunt/compound-engineering.local.md](/Users/simons/job-hunt/compound-engineering.local.md)

## Acceptance Criteria

- [ ] The plan explicitly distinguishes winner selection from observed-source retention
- [ ] A durable additive provenance shape is named, such as `observed_sources[]`
- [ ] The plan defines how precedence chooses `primary_source` without erasing non-winning provenance
- [ ] Tests/docs are updated to cover duplicate-source auditability

## Work Log

### 2026-04-22 - Provenance Retention Confirmed

**By:** Codex

**Actions:**
- Re-reviewed the plan and confirmed it now separates winner selection from observed-source retention.
- Verified the plan names `observed_sources[]`, keeps `lead.source` as a compatibility alias, and includes provenance-focused acceptance criteria and quality gates.
- Closed this todo because the additive provenance shape is now explicit in the plan.

**Learnings:**
- Auditability got materially better once the plan treated precedence and retention as separate contracts.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Synthesized agent-native and review-context findings around provenance retention
- Created a todo to track additive provenance requirements

**Learnings:**
- The current plan is strong on precedence but still underspecified on preserving the losing-side evidence
