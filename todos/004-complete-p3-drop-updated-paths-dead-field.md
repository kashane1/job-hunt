---
status: pending
priority: p3
issue_id: 004
tags: [code-review, simplicity, cleanup]
dependencies: []
---

# Drop `recompute_tiers.updated_paths` — dead field

## Problem Statement

`recompute_tiers` returns a summary dict with an `updated_paths` list that no consumer reads. CLI just dumps the whole dict; tests assert on `updated` count only. Per-record allocation + 3 LOC of maintenance for zero value.

## Findings

- `src/job_hunt/application.py:665, 683, 688` — `updated_paths` write sites
- Neither the CLI subcommand (core.py) nor any test reads `result["updated_paths"]`.

## Proposed Solutions

**A. Remove the list and the field.** Counts are enough for the CLI summary.

## Recommended Action

Option A.

## Acceptance Criteria

- [ ] `updated_paths: list[str] = []` removed from `recompute_tiers`.
- [ ] `updated_paths.append(...)` inside the loop removed.
- [ ] `"updated_paths": updated_paths` field removed from the return dict.
- [ ] Tests still pass (they never asserted on the field).

## Resources

- Review: code-simplicity-reviewer findings on PR #3
- File: src/job_hunt/application.py:649-689
