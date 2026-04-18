---
status: pending
priority: p3
issue_id: 006
tags: [code-review, docs]
dependencies: []
---

# Fix `parse_retry_after` docstring "clamping" overpromise

## Problem Statement

Docstring promises "Negative (past) dates are clamped to 0" — but a literal negative delta-seconds string like `"-5"` returns `None` (because `str.isdigit()` is False on `-`, and `parsedate_to_datetime("-5")` fails). RFC 9110 §10.2.3 explicitly forbids negative deltas, so returning `None` is defensible — the docstring just needs to match.

## Findings

- `src/job_hunt/net_policy.py` — `parse_retry_after` docstring

## Proposed Solutions

**A. Tighten the docstring.** Clamping applies only to past HTTP-dates, not to literal negative integers.

## Recommended Action

Option A.

## Acceptance Criteria

- [ ] Docstring reads: "Past HTTP-dates are clamped to 0; negative delta-seconds return None per RFC 9110."
- [ ] Add a unit test: `parse_retry_after("-5") is None`.

## Resources

- Review: kieran-python-reviewer findings on PR #3
- RFC 9110 §10.2.3
