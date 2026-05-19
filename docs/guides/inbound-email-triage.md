# Inbound Email → Status Triage — Runbook

Triage classifies inbound recruiter/ATS email and advances the matched
lead's application status so `calibrate-scoring` is fed automatically
(no manual `update-status` per rejection). It reuses the proven
`confirmation.py` verification pipeline and writes Model B
(`data/applications/{lead_id}-status.json`) — the only status the learning
loop reads.

## Trust model (read this first)

Triage **never fabricates a transition**. It writes a status change only
when:

1. the sender is in the ATS allowlist **and** DKIM passes, **or**
2. the DKIM `header.d=` registrable domain (not `From`, not display name,
   not body) **equals the stored company domain** for exactly one lead, and
   the company token corroborates in the subject/body.

Anything else — ambiguous, no match, unverified, unclassified — is
**quarantined** to `data/applications/_suspicious/` (subject/body redacted)
and surfaced by `check-integrity`. A rejection/offer from a non-allowlisted
sender is **always** quarantined for human promotion, even if its DKIM
domain matches (anti-spoof: an outcome is the highest-value forgery target).

Triage is idempotent (re-running a batch is safe), never moves a stage
backward, and tags >1-stage jumps `inferred_skip` so analytics excludes
them from funnel learning.

## One full cycle

```bash
# 1. Get the Gmail query (the agent runs the actual fetch via the Gmail MCP)
python3 scripts/job_hunt.py triage-inbox --emit-query --window-days 14
#    → {"status":"ok","gmail_query":"from:(...) newer_than:14d subject:(...)"}

# 2. Agent fetches matching messages and writes them as a JSON list
#    (each item: a Gmail API message dict, or a path to an .eml file).

# 3. Dry-run — classify + correlate, ZERO writes. Inspect before applying.
python3 scripts/job_hunt.py triage-inbox --inbox-file inbox.json --dry-run
#    → per message: {label, matched_rule, correlation, lead_id}

# 4. Apply. Verified+correlated emails advance Model B; the rest quarantine.
python3 scripts/job_hunt.py triage-inbox --inbox-file inbox.json
#    → {"status":"ok","advanced":N,"quarantined":M,"noop":K,"results":[...]}

# 5. Ghost timeouts (run on a slow schedule, e.g. weekly): leads stuck in a
#    non-terminal stage with no activity for N days → ghosted.
python3 scripts/job_hunt.py triage-ghosts --days 21 --dry-run   # preview
python3 scripts/job_hunt.py triage-ghosts --days 21             # apply

# 6. Feed the loop.
python3 scripts/job_hunt.py calibrate-scoring
```

## Reviewing quarantine

Quarantined messages land in `data/applications/_suspicious/*.json`
(redacted) and are counted by `check-integrity`
(`quarantined_confirmations`). A real rejection/offer from a recruiter on a
non-allowlisted domain is the expected case here. Resolve it through the
`triage-review-*` triad (agent-native parity with discovery `review-*`):

```bash
# 1. List quarantined entries, each with a re-derived {lead_id, stage}
#    proposal (subject/body are NOT echoed — PII hygiene).
python3 scripts/job_hunt.py triage-review-list

# 2. Propose for one message (ZERO writes — inspect before applying).
python3 scripts/job_hunt.py triage-review-promote <message_id>
#    → {"status":"proposed","proposal":{lead_id,to_stage,matched_rule,...}}

# 3. Apply. The proposal is derived from the SUBJECT only (the body is
#    never persisted), so when correlation/classification is not
#    confident, supply --lead / --stage explicitly after verifying the
#    email in Gmail. --confirm is mandatory to write anything.
python3 scripts/job_hunt.py triage-review-promote <message_id> --confirm
python3 scripts/job_hunt.py triage-review-promote <message_id> \
    --lead L1 --stage rejected --confirm

# Not a real outcome (spam / mis-sent)? Dismiss with a mandatory reason.
python3 scripts/job_hunt.py triage-review-dismiss <message_id> \
    --reason "recruiter spam, not an outcome for any lead"
```

Promote bridges Model B through the same locked, idempotent
`_bridge_to_stage` as `triage-inbox` (re-promoting is a `noop_duplicate`,
never a double transition; contention keeps the file for a safe retry).
Resolving an entry — promote *or* dismiss — deletes the quarantine file and
appends the action to `data/applications/_suspicious/.audit.jsonl`, so
`check-integrity.quarantined_confirmations` reflects only *unresolved*
entries (the count no longer grows monotonically). The audit log is a
dotted `.jsonl`, deliberately outside the `_suspicious/*.json` glob, so it
never re-inflates the count.

## Divergence / replay

`check-integrity.unbridged_confirmations` lists Model-A confirmation events
(`{draft_id}/status.json`) whose outcome never reached Model B — e.g. a
crash between the two writes. Re-running `triage-inbox` over the same
messages replays them safely (idempotent on `event_id`).

## Failure codes

`triage-inbox` emits `{"status":"error", ...}` + exit code 2 on bad input
(`triage_invalid_input`). Per-message failures never abort the batch —
they are quarantined and reported in `results[]`.
