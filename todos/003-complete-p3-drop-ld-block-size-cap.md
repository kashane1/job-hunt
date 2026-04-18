---
status: pending
priority: p3
issue_id: 003
tags: [code-review, simplicity, cleanup]
dependencies: []
---

# Drop `_MAX_LD_BLOCK_BYTES` — redundant with fetch's 2MB cap

## Problem Statement

`_MAX_LD_BLOCK_BYTES = 512_000` in `src/job_hunt/ingestion.py` and the `if len(raw) > _MAX_LD_BLOCK_BYTES: continue` check add defensive ceremony that `fetch()`'s existing `MAX_FETCH_BYTES = 2_000_000` already structurally bounds. A single LD+JSON block cannot exceed the whole response body, which is already capped.

## Findings

- `src/job_hunt/ingestion.py:694` — constant definition
- `src/job_hunt/ingestion.py:747-748` — size-gate that's never exercised by normal input
- `fetch()` already caps the entire response at 2 MB; no single `<script>` block can exceed that.

## Proposed Solutions

**A. Delete the constant + the size check.** ~5 LOC saved.

## Recommended Action

Option A.

## Acceptance Criteria

- [ ] `_MAX_LD_BLOCK_BYTES` removed.
- [ ] `if len(raw) > _MAX_LD_BLOCK_BYTES: continue` removed.
- [ ] The existing test `test_rejects_oversized_ld_block` needs adjusting — either rewrite it to test the 2 MB fetch cap, or simply delete it since the fetch layer covers the same invariant.
- [ ] All ingestion tests still pass.

## Resources

- Review: code-simplicity-reviewer findings on PR #3
- File: src/job_hunt/ingestion.py:690-770
