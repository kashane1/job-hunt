---
status: complete
priority: p3
issue_id: "073"
tags: [code-review, robustness, triage]
dependencies: []
---

# Triage robustness nits (KeyError risk, silent drops, label clarity)

## Problem Statement

Small hardening items from PR #4 review, none blocking:

1. `scan_ghost_timeouts` uses raw `transitions[-1]["timestamp"]`; a
   legacy/hand-edited Model-B record missing `timestamp` raises `KeyError`
   and aborts the whole sweep (the `try/except` only wraps `read_json`).
   Use `.get("timestamp", "")`.
2. `core.py` triage-inbox loop silently drops inbox items that are neither
   `dict` nor `str` — add a `skipped_unparseable` count to the rollup.
3. `BridgeResult.outcome` overloads `"noop_backward"` for same-stage and
   for `assessment_request`/`unknown` (no-stage). Add `noop_same_stage` /
   `noop_no_stage` so the rollup is honest.
4. `tests/test_triage.py` / `test_calibration.py` lack a module docstring
   (most newer test files have one).

## Recommended Action

Apply all four; each is a 1–3 line change.

## Acceptance Criteria

- [ ] `scan_ghost_timeouts` cannot KeyError on a malformed transition.
- [ ] Unparseable inbox items are counted, not silently dropped.
- [ ] No-op outcome literals distinguish backward vs same-stage vs no-stage.
- [ ] Full suite green.

## Work Log

- 2026-05-18: Found by data-integrity-guardian + kieran-python-reviewer (PR #4).

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
