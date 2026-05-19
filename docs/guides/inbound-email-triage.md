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
(`quarantined_confirmations`). Review them by hand: a real
rejection/offer from a recruiter on a non-allowlisted domain is the
expected case here — promote it by running the manual
`update-status` for that lead after you have verified the email.

## Divergence / replay

`check-integrity.unbridged_confirmations` lists Model-A confirmation events
(`{draft_id}/status.json`) whose outcome never reached Model B — e.g. a
crash between the two writes. Re-running `triage-inbox` over the same
messages replays them safely (idempotent on `event_id`).

## Failure codes

`triage-inbox` emits `{"status":"error", ...}` + exit code 2 on bad input
(`triage_invalid_input`). Per-message failures never abort the batch —
they are quarantined and reported in `results[]`.
