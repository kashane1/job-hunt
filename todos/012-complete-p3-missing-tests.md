---
status: pending
priority: p3
issue_id: "012"
tags: [code-review, python, testing]
dependencies: []
---

# Add missing test cases identified by reviewers

## Problem Statement

Several important test cases were identified as missing from the plan's test lists.

## Findings

Missing tests:
1. **Accomplishment overlap threshold** -- Success metric says < 50% overlap between variants. No test asserts this.
2. **Knockout false-positive resistance** -- "What is your experience with authorization systems?" should NOT match work_authorization knockout.
3. **Bank matching backward compatibility** -- Best match from multi-match should equal what old single-match would have returned.
4. **Jaccard empty-set guard** -- Division by zero when both token sets are empty.
5. **Accomplishment relevance in isolation** -- Unit test for exact match > partial > zero overlap.
6. **Adversarial bank matching** -- "Tell me about yourself" against 28 entries should score below threshold.

## Proposed Solutions

Add these to the Phase 2 and Phase 3 test plans during implementation.

**Effort:** Small per test
**Risk:** Low

## Acceptance Criteria

- [ ] All 6 test cases added to the appropriate test files
- [ ] Tests pass

## Work Log

### 2026-04-16 - Discovery

**By:** Python reviewer
