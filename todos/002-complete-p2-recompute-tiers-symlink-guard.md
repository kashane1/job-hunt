---
status: pending
priority: p2
issue_id: 002
tags: [code-review, security, data-integrity]
dependencies: []
---

# Guard `recompute_tiers` against symlink writeback

## Problem Statement

`recompute_tiers` in `src/job_hunt/application.py` iterates `applications_dir.glob("*-status.json")`, which follows symlinks by default. A status-file symlink pointing outside the applications directory would be both **read** and **written** via `write_json` — enabling a targeted overwrite of any user-readable JSON file whose contents match the demotion criteria (`tier=="tier_2"` AND `tier_rationale=="ats_status:warnings"`).

Threat model is narrow (single-user CLI; attacker would already need write access to plant the symlink), but the write-back pattern is the concerning piece.

## Findings

- `src/job_hunt/application.py:666` — `for status_path in applications_dir.glob(...)` follows symlinks.
- `src/job_hunt/application.py:681` — `write_json(status_path, record)` follows the symlink on write too.

## Proposed Solutions

**A. Skip symlinks explicitly.**

```python
if status_path.is_symlink():
    skipped += 1
    continue
```

Add right after `scanned += 1`. Minimal, no behavioral change for the single-user case.

**B. Resolve and verify parent.** Check that `status_path.resolve().parent == applications_dir.resolve()` before writing.

## Recommended Action

Option A — one line, obvious, matches the rest of the CLI's "skip weirdness" idioms.

## Technical Details

- Affected file: `src/job_hunt/application.py`
- One-line addition.

## Acceptance Criteria

- [ ] `recompute_tiers` skips symlinked status files with a `skipped += 1` bump.
- [ ] Regression test: create a symlink inside applications_dir pointing outside, assert it's skipped and not overwritten.

## Resources

- Review: security-sentinel findings on PR #3
- File: src/job_hunt/application.py:666
