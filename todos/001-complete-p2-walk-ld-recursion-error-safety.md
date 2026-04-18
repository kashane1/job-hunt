---
status: pending
priority: p2
issue_id: 001
tags: [code-review, security, defensive]
dependencies: []
---

# Guard `_walk_ld_nodes` against uncaught `RecursionError`

## Problem Statement

`_walk_ld_nodes` in `src/job_hunt/ingestion.py` recurses through arbitrary JSON-LD trees without a depth guard. Python's default recursion limit (~1000) will raise `RecursionError` on maliciously deep JSON. The `try/except (json.JSONDecodeError, ValueError, RecursionError)` around `json.loads` catches parse-time recursion but NOT the walker — `_walk_ld_nodes` is called AFTER `json.loads` succeeds, so a deeply-nested-but-valid payload crashes the whole viewjob fetch.

## Findings

- `src/job_hunt/ingestion.py:710-721` — `_walk_ld_nodes` recurses unbounded.
- `src/job_hunt/ingestion.py:755` — the `for node in _walk_ld_nodes(data)` iteration runs outside the json.loads try/except.
- Single posting failure poisons the whole ingest, not just one block.

## Proposed Solutions

**A. Wrap the walker loop in try/except.** Low-risk; contains the blast radius to one posting.

```python
try:
    for node in _walk_ld_nodes(data):
        ...
except RecursionError:
    continue  # skip this LD+JSON block, keep looking
```

**B. Add an explicit depth parameter to `_walk_ld_nodes`.** Stronger but more intrusive.

## Recommended Action

Option A — minimal, bounded, obvious.

## Technical Details

- Affected file: `src/job_hunt/ingestion.py`
- One-line try/except addition around the walker loop.
- No test change needed; existing tests cover the happy path.

## Acceptance Criteria

- [ ] `_walk_ld_nodes` invocation is wrapped in `try/except RecursionError: continue`.
- [ ] Existing viewjob tests still pass.
- [ ] Add a regression test: deeply nested valid JSON-LD (>1000 levels) falls back to the `#jobDescriptionText` path instead of raising.

## Resources

- Review: security-sentinel findings on PR #3
- File: src/job_hunt/ingestion.py:710-755
