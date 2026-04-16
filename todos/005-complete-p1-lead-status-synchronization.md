---
status: pending
priority: p1
issue_id: "005"
tags: [code-review, data-integrity]
dependencies: []
---

# Define synchronization between lead.status and application current_stage

## Problem Statement

The existing `lead.schema.json` has a `status` field (values: "discovered", "shortlisted", "skipped"). The new `application-status.schema.json` introduces `current_stage` (values: "not_applied" through "ghosted"). These are two independent status systems on the same entity with no defined synchronization. When the application moves to "rejected," the lead remains "shortlisted."

## Findings

- `score_lead` in core.py sets lead status to "shortlisted" or "skipped" (line 1314)
- The new `update-status` command updates application status but never touches lead status
- `summarize-run` counts by lead status, which will be stale after application tracking is added
- `check-follow-ups` uses application status, not lead status

## Proposed Solutions

### Option 1: Lead status is the discovery-phase status; application status is the lifecycle status (Recommended)

**Approach:** Document that `lead.status` tracks discovery (discovered/shortlisted/skipped) and `application_status.current_stage` tracks the application lifecycle. They serve different purposes and do not need synchronization. Update `summarize-run` to read from both.

**Effort:** Small -- documentation + minor code update to summarize-run
**Risk:** Low

### Option 2: Synchronize automatically

**Approach:** When `update-status` moves to "applied", also set lead status to "applied". Keep them in sync.

**Effort:** Medium -- adds coupling between tracking.py and lead files
**Risk:** Medium -- creates a maintenance burden

## Recommended Action

Option 1. The two status concepts serve different purposes. Document clearly.

## Acceptance Criteria

- [ ] Plan documents the relationship between lead.status and application current_stage
- [ ] summarize-run uses the correct status source for each metric
- [ ] No code assumes lead.status reflects application lifecycle

## Work Log

### 2026-04-16 - Discovery

**By:** Data integrity review agent
