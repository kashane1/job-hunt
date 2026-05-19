---
status: complete
priority: p2
issue_id: "071"
tags: [code-review, testing, triage]
dependencies: []
---

# STAGE_LADDER consistency test is tautological (doesn't guard the real risk)

## Problem Statement

`tests/test_triage.py` `test_ladder_matches_analytics_sequence` asserts
`[STAGE_LADDER[s] for s in STAGE_SEQUENCE] == sorted(...)`. Since
`STAGE_LADDER` is *derived* from `STAGE_SEQUENCE` via `enumerate`, this
passes for any input — it tests `enumerate`, not an invariant. The real
drift risk is between `confirmation._PRIORITY` (an independent,
hand-maintained dict) and `STAGE_SEQUENCE` ordering; the docstring claims
the test guards "triage, analytics, **and confirmation** never disagree"
but `_PRIORITY` is never asserted.

## Recommended Action

Strengthen the test to assert the shared-key subset of
`confirmation._PRIORITY` is monotonic in `STAGE_SEQUENCE` order, so a
future edit to `STAGE_SEQUENCE` that desyncs confirmation's promotion
ladder fails loudly.

## Acceptance Criteria

- [ ] Test fails if `confirmation._PRIORITY` ordering disagrees with `STAGE_SEQUENCE` on shared stages.
- [ ] Full suite green.

## Work Log

- 2026-05-18: Found by architecture-strategist (PR #4).

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
