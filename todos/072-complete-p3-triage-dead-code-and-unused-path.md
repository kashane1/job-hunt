---
status: complete
priority: p3
issue_id: "072"
tags: [code-review, simplicity, triage]
dependencies: ["068"]
---

# Triage dead code + unexercised confirmation-event path

## Problem Statement

`triage_inbox` classifies/bridges everything through the recruiter path
(`classify_recruiter_email` + `bridge_recruiter`), even allowlisted ATS
emails. Consequently `bridge_event`, `event_id_for`, `_EVENT_TO_STAGE`,
and the only in-module `TriageError` raise are exercised **only by their
own tests** — and the cross-model A/B `event_id` alignment they exist for
is never actually used in production. Also unused: `CorrelationIndex.by_jk`
/ `by_posting_url` (and the whole plan.json glob loop that fills them),
`BridgeResult.to_dict`, and `OUTCOME_STAGES`'s `"withdrawn"` (no label/event
targets it).

## Recommended Action

Resolve the design inconsistency first (depends on todo 068's decision):
either (a) route allowlisted/confirmation emails through `bridge_event` so
A/B idempotency alignment is real, then keep it + add a production test; or
(b) delete the dead confirmation-event path, `CorrelationIndex.by_jk/
by_posting_url` + their loop, `BridgeResult.to_dict`, and narrow
`OUTCOME_STAGES` to `{"rejected","ghosted"}`. ~90 LOC src + ~80 LOC tests.
No trust invariant touched (no production caller).

## Acceptance Criteria

- [ ] Either the confirmation-event path has a production caller + test, or it (and the listed unused members) are removed.
- [ ] Full suite green.

## Work Log

- 2026-05-18: Found by code-simplicity-reviewer (PR #4).

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
