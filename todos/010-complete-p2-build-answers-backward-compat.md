---
status: pending
priority: p2
issue_id: "010"
tags: [code-review, architecture, python]
dependencies: []
---

# Do not modify _build_answers in core.py -- use new code path

## Problem Statement

Phase 3 says it will "update `_build_answers()` in `core.py` to use the new matching (backward compatible)." But `_build_answers` has a `break` after the first match (line 1406) that consumers depend on. Changing this behavior alters draft outputs.

## Findings

- Architecture review flagged this as a backward compatibility risk
- The new `generate-answers` CLI command should be the new code path
- `_build_answers` and `build-draft` should remain unchanged

## Proposed Solutions

### Option 1: Keep _build_answers unchanged (Recommended)

**Approach:** `generate-answers` calls new matching functions in `generation.py`. `build-draft` continues to call `_build_answers` from `core.py` unchanged. The new path is additive, not a modification.

**Effort:** None (design decision, not code)
**Risk:** Low

## Acceptance Criteria

- [ ] `_build_answers` in core.py is not modified
- [ ] `generate-answers` uses new matching from generation.py
- [ ] Existing `build-draft` tests continue to pass unchanged

## Work Log

### 2026-04-16 - Discovery

**By:** Architecture reviewer, Python reviewer
