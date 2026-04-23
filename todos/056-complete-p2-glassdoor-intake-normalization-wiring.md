---
status: complete
priority: p2
issue_id: "056"
tags: [code-review, plan, architecture, intake, glassdoor]
dependencies: []
---

# Wire Glassdoor manual-intake normalization through extract_lead

## Problem Statement

The original plan added `GlassdoorBoardAdapter.normalize_manual_intake()` but
did not name the actual shared intake entrypoint that must invoke it.

Without that wiring, the normalization work would exist only on paper.

## Findings

- Glassdoor manual artifacts need to be normalized before persistence.
- The original plan omitted the shared intake seam that performs that work.
- The plan has been updated to require `core.extract_lead()` to invoke the
  adapter normalization path for `glassdoor_manual`.

## Proposed Solutions

### Option 1: Normalize in extract_lead

**Approach:** Route manual-intake metadata through board adapter normalization
at the shared entrypoint.

**Pros:**
- Matches existing architecture
- Keeps normalization near lead creation

**Cons:**
- Requires careful adapter lookup before persistence

**Effort:** 2-4 hours

**Risk:** Low

---

### Option 2: Normalize later during prepare_application

**Approach:** Defer Glassdoor normalization until application preparation.

**Pros:**
- Smaller change to intake path

**Cons:**
- Loses normalized provenance in stored lead artifacts
- Pushes board-specific cleanup later than necessary

**Effort:** 2-3 hours

**Risk:** Medium

## Recommended Action

Resolved by wiring `core.extract_lead()` through
`GlassdoorBoardAdapter.normalize_manual_intake()` for `origin_board=glassdoor`.

## Technical Details

**Affected files:**
- `src/job_hunt/core.py`
- `src/job_hunt/boards/glassdoor.py`
- intake tests

## Acceptance Criteria

- [ ] `extract_lead()` invokes Glassdoor manual-intake normalization
- [ ] Persisted Glassdoor leads retain normalized routing and provenance data
- [ ] Tests cover the normalization path end-to-end

## Work Log

### 2026-04-20 - Review finding created

**By:** Codex

**Actions:**
- Reviewed how manual-intake normalization would actually be reached
- Identified missing wiring to the shared intake path
- Updated the plan to require `extract_lead()` integration

**Learnings:**
- Plan-level architecture often fails at the caller seam, not the new module
  seam

### 2026-04-21 - Resolution

**By:** Codex

**Actions:**
- Added `GlassdoorBoardAdapter.normalize_manual_intake()`
- Updated `extract_lead()` to invoke it before persistence
- Added intake tests for hosted and redirecting Glassdoor manual artifacts

**Learnings:**
- The end-to-end extract path is the only place this normalization really becomes durable system behavior
