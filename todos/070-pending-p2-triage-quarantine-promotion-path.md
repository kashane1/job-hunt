---
status: pending
priority: p2
issue_id: "070"
tags: [code-review, agent-native, triage]
dependencies: ["068"]
---

# Quarantined triage outcomes are not promotable (agent or human)

## Problem Statement

Non-allowlisted recruiter outcomes quarantine to `_suspicious/` (correctly,
esp. after todo 068). But the quarantine JSON stores
`sender/subject/message_id/...` and **no `lead_id`, no proposed stage** —
because it quarantined precisely when correlation/classification was not
confident. The documented manual fallback (`update-status --lead PATH
--stage X`) is therefore not executable from quarantine state by agent or
human without re-deriving lost data, and resolved quarantine files are
never garbage-collected (`check-integrity` count grows monotonically).

## Recommended Action

Add a `triage-review` surface (the consciously-deferred command, now with
concrete shape): `triage-review-list` / `triage-review-promote
<message_id>` / `triage-review-dismiss <message_id> --reason`. Promote
re-runs the pure `correlate_recruiter` + `classify_recruiter_email` to emit
a *proposed* `{lead_id, to_stage, matched_rule}`; on confirm it calls
`_bridge_to_stage` and removes the quarantine file. All primitives already
exist and are import-clean; only the orchestration wrapper is missing.
Mirror discovery's `review-list/-promote/-dismiss` triad.

## Acceptance Criteria

- [ ] `triage-review-list` enumerates `_suspicious/` triage entries (JSON).
- [ ] `triage-review-promote <message_id>` proposes + (on confirm) bridges + deletes the quarantine file.
- [ ] `triage-review-dismiss <message_id> --reason` removes it with an audit reason.
- [ ] Resolved entries no longer counted by `check-integrity`.

## Work Log

- 2026-05-18: Found by agent-native-reviewer (PR #4). Blocked by 068 (defines what quarantines).

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
- Pattern: discovery `review-list/-promote/-dismiss` in `core.py`
