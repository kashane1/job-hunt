---
title: Bridge the confirmation status model to the tracking ledger (don't unify, bridge)
date: 2026-05-18
module: triage
problem_type: integration_issue
component: src/job_hunt/triage.py
symptoms:
  - A feature that "records outcomes" updated confirmation's status.json but the analytics/learning loop never moved off insufficient_data
  - Two status models existed (lifecycle_state/events vs current_stage/transitions) and only one was read by calibrate-scoring
  - Stage-skip and ghosted-reactivation silently corrupted funnel stats fed to the learning loop
tags:
  - two-models
  - status-ledger
  - idempotency
  - dkim
  - anti-spoof
  - learning-loop
severity: high
---

# Bridge the confirmation status model to the tracking ledger

## Summary

When a codebase has accreted **two status models** — here Model A
(`{draft_id}/status.json`, `lifecycle_state`/`events[]`, written by
`confirmation.py`) and Model B (`{lead_id}-status.json`,
`current_stage`/`transitions[]`, written by `tracking.py`) — and only one
of them feeds the consumer you care about (`calibrate-scoring` reads Model
B only), the highest-leverage move is usually **not** to unify them. Unify
later; **bridge** now. The bridge is small, reversible, and detectable;
unification is a high-blast-radius refactor that does not need to be on the
critical path of "feed the loop."

## What did not work / the trap

The obvious implementation — "extend `confirmation.update_status` to also
classify recruiter email" — fails three ways at once:

1. **Wrong consumer.** `confirmation` writes Model A; `calibrate-scoring`
   reads Model B. The data never arrives. (Caught only because a reviewer
   asked "which file does the loop actually read?")
2. **Import cycle.** Hooking the bridge into `poll_confirmations` makes
   `confirmation → triage` while `triage → confirmation` already holds.
3. **Silent consumer corruption.** Even a correct bridge feeds garbage if
   the consumer can't represent what the bridge writes: a stage-skip
   (`applied → offer`) inflates conversion rates, and a `ghosted →
   reactivated` lead vanishes from the ghost bucket because the consumer
   filtered on `current_stage` instead of transition history.

The third is the subtle one: **fixing the producer is not enough; the
consumer's vocabulary has to be able to represent the new writes.** The
plan's first draft explicitly scoped "analytics untouched" — that was the
bug.

## Working solution

- A standalone `triage.py` bridge: `triage → confirmation` and
  `triage → tracking`, one direction, no cycle. `confirmation` is never
  modified.
- `tracking` gains a lock-free `_apply_transition_locked` core; the public
  path and the bridge both go through one `file_lock` acquisition
  (single-lock pre-validate-and-write closes the TOCTOU).
- Idempotency is decided by **inspecting Model B's own transitions** for
  the shared `event_id` — never by the producer's return value
  (`confirmation.update_status` returns the same dict whether it appended
  or deduped, so it cannot be trusted as a discriminator).
- The consumer was changed in the same feature: `inferred_skip` transitions
  are excluded from `_stage_conversions`/`_last_non_terminal_stage`/
  `_applied_date`; terminal outcome is derived from transition **history**
  (`_terminal_outcome`) so a reactivated ghost is still counted.
- Trust binding for the new (non-URL) correlation path is **DKIM
  `header.d=` registrable-domain equality with the stored company domain**,
  never `From`/display-name/body. Registrable-domain equality rejects
  lookalikes inherently (`stripe-careers.com` ≠ `stripe.com`;
  `stripe.com.evil.net` reduces to `evil.net`). Outcomes from
  non-allowlisted senders quarantine for human promotion (anti-spoof).
- A↔B divergence is **detected** (`check-integrity.unbridged_confirmations`)
  and **replayable** (`triage-inbox`, idempotent), not claimed-prevented —
  Model A is written before Model B, so a crash between them is possible.

## Prevention strategies

1. **"Which file does the consumer actually read?"** is the first question
   for any feature that claims to feed a downstream system. Grep the
   consumer's loader, don't assume the writer you know about is the one it
   reads.
2. **A producer change that the consumer cannot represent is a corruption,
   not a feature.** Scope the consumer fix into the same change; "leave the
   consumer untouched" is a smell when you're adding a new record shape.
3. **Bridge before unify.** Two models with one bridge + a divergence
   integrity check is a shippable seam; model unification is a separate,
   later, high-risk refactor.
4. **Idempotency keys must be inspectable on the durable record**, not
   inferred from a non-discriminating return value.
5. **Bound every regex over untrusted external text.** An unbounded `+`
   over a long no-terminator run is quadratic — a real ReDoS surfaced here
   only because a unit test took 80 s. Bounded quantifiers ({1,64} etc.)
   are the linear-time fix.

## Related

- `docs/plans/2026-05-18-001-feat-inbound-email-status-triage-plan.md` (deepened)
- `docs/solutions/security-issues/design-secret-handling-as-a-runtime-boundary.md`
- `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md`
- Closes the loop opened by `calibrate-scoring` (commit `a303c58`).
