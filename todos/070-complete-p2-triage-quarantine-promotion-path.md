---
status: complete
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

- [x] `triage-review-list` enumerates `_suspicious/` triage entries (JSON).
- [x] `triage-review-promote <message_id>` proposes + (on confirm) bridges + deletes the quarantine file.
- [x] `triage-review-dismiss <message_id> --reason` removes it with an audit reason.
- [x] Resolved entries no longer counted by `check-integrity`.

## Work Log

- 2026-05-18: Found by agent-native-reviewer (PR #4). Blocked by 068 (defines what quarantines).
- 2026-05-18: Implemented. Added `list_triage_quarantine` /
  `promote_triage_quarantine` / `dismiss_triage_quarantine` to `triage.py`
  (pure orchestration over the existing tested primitives —
  `correlate_recruiter`, `classify_recruiter_email`, `_bridge_to_stage`;
  TrustInvariantTest still green, no argparse/LLM/env in the module) and
  the matching `triage-review-list/-promote/-dismiss` CLI triad in
  `core.py`, mirroring discovery's `review-*` block. Decisions beyond the
  brief: (a) **promote is propose-by-default; writes only under explicit
  `--confirm`** — stricter than discovery's immediate-apply, because an
  outcome bridged to Model B feeds `calibrate-scoring`, and AGENTS.md's
  anti-spoof invariant is "never silently applied"; `--lead`/`--stage`
  override when the subject-only re-derivation (the body is never
  persisted) is not confident. (b) Resolving deletes the file **and**
  appends to `_suspicious/.audit.jsonl` — a dotted `.jsonl` deliberately
  outside check-integrity's `*.json` glob, so the trust decision stays
  auditable without re-inflating `quarantined_confirmations`. (c)
  Path-traversal fenced via the same sanitization confirmation._quarantine
  used to write the file + a resolved-parent containment check. Idempotent
  on a deterministic `review_promote` event_id (re-promote ⇒
  `noop_duplicate`, contention keeps the file for retry). 10 new tests
  (incl. CLI round-trip + traversal); full suite 612 → 612 (+10) green.
  Docs updated: AGENTS.md invariant, README, the triage runbook, and
  batch-4-apply.md's `quarantined_confirmations` note.

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
- Pattern: discovery `review-list/-promote/-dismiss` in `core.py`
