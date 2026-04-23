---
status: pending
priority: p3
issue_id: "067"
tags: [code-review, plan-review, docs, config, discovery]
dependencies: []
---

# Sync `config/sources.yaml` with actual runtime source support

## Problem Statement

The plan describes the current discovery set narrowly, but the repo’s source catalog already advertises sources that the runtime does not actually accept.

This matters because operator-facing catalogs and docs should not claim support that the runtime does not implement.

## Findings

- The plan describes the current supported set as `greenhouse`, `lever`, `indeed_search`, and `careers`.
- `config/sources.yaml` already advertises `ashby` and `workday` while the runtime discovery token list still only accepts the narrower set.

## Proposed Solutions

### Option 1: Add `config/sources.yaml` to the plan’s file list and update it in lockstep

**Approach:** Treat the source catalog as part of the rollout surface and require it to stay consistent with actual provider support.

**Pros:**
- Improves operator trust
- Keeps docs/config aligned with runtime

**Cons:**
- Slightly larger documentation/config surface

**Effort:** 15-30 minutes

**Risk:** Low

---

### Option 2: Narrow the catalog immediately before new provider work lands

**Approach:** Remove unsupported sources from the catalog now, then re-add them only when support is real.

**Pros:**
- Simplest consistency fix

**Cons:**
- Separate cleanup step

**Effort:** 15-30 minutes

**Risk:** Low

## Recommended Action

To be filled during triage.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- `/Users/simons/job-hunt/config/sources.yaml`

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)

## Acceptance Criteria

- [ ] The plan accounts for `config/sources.yaml` or an equivalent source catalog
- [ ] Operator-facing source listings match actual runtime support
- [ ] Unsupported source names are not advertised ahead of implementation

## Work Log

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Captured the source-catalog mismatch from the repo-structure review
- Added a lower-priority todo because this is a consistency issue, not a blocking design flaw

**Learnings:**
- Discovery support has more operator-facing surfaces than just the registry and watchlist
