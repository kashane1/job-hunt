---
name: check-replies
description: Check Gmail for replies to job applications, triage them into the ledger (human-approved), and show the live pipeline. Use when the user asks "did anyone reply", "check my email for job apps", "any recruiter responses", or wants their application pipeline status.
---

# check-replies

Fetches recent Gmail replies via the session's Gmail MCP, runs them through the
existing `triage-inbox` write path, and renders the live pipeline. The actual
Gmail fetch is **agent-driven** (this repo stores no Google credentials); the
CLI emits the query and ingests the JSON the agent assembles.

## Invariants (do not violate)

- **Propose-only.** NEVER apply ledger writes without explicit user approval.
  Always run the dry-run preview first, show it, and wait for the user to say
  "apply" (or equivalent) before the writing step. This is the gate — there is
  no auto-apply.
- **Raw payload only.** Feed `triage-inbox` the Gmail MCP's raw message output
  verbatim. Do NOT pre-classify, pre-filter to "only allowlisted senders", drop
  messages, or synthesize/edit `Authentication-Results`/`From`. The trust
  guarantees (sender allowlist, DKIM, correlation) live in `triage.triage_inbox`
  and only hold if the agent is a dumb transport. (Regression:
  `tests/test_triage.py::TriageInboxAntiSpoofTest`.)
- **Human recruiters never auto-advance.** Only allowlisted ATS senders with
  `dkim=pass` and a single correlated draft advance. Everything else quarantines
  for human promotion — surface it, don't force it.

## Commands

All CLI calls run via the project entrypoint, e.g.:
`PYTHONPATH=src python3 -c "from job_hunt.core import main; main([...])"`
(There is no console-script; `core.main(argv)` is the entrypoint.)

## Steps

1. **Emit the query.**
   `triage-inbox --emit-query` → prints the Gmail search string (allowlisted ATS
   senders, recency window, application-related subjects).

2. **Fetch via Gmail MCP.** Run that query with `search_threads`, then
   `get_thread` for each hit to get full message bodies. Assemble a JSON **list**
   of raw Gmail messages (headers incl. `From`, `Message-ID`, `Subject`,
   `Authentication-Results`; plus body). Write it to a scratch file, e.g.
   `<scratchpad>/inbox.json`. Do not edit or filter the messages.

3. **Dry-run preview (mandatory).**
   `triage-inbox --inbox-file <scratchpad>/inbox.json --dry-run`
   Classifies and correlates with **zero writes**. Show the user the rollup:
   what would advance (lead + stage), what would quarantine, what is a no-op.

4. **Get explicit approval.** Ask the user to confirm. Only proceed to apply if
   they explicitly approve. If they decline, stop — nothing was written.

5. **Apply (only after approval).**
   `triage-inbox --inbox-file <scratchpad>/inbox.json`
   (no `--dry-run`). Idempotent: re-running on the same messages is a safe no-op
   (Model A/B dedup on `event_id`), and quarantine files are keyed by Message-ID.

6. **Render the pipeline.**
   `pipeline-summary` → JSON: `totals`, `open_ranked` (live apps furthest-along
   first, each with its full `transitions[]` timeline and `next_follow_up_date`),
   `closed_counts`, and `quarantine` (the needs-you queue). Render it for the
   user as:
   - a one-line **totals** roster (open by stage + closed counts),
   - the **furthest-along open apps** with their full timelines (newest event
     last; flag any `next_follow_up_date`),
   - a **NEEDS YOU** section for `quarantine` entries (human recruiters etc.),
     each with the follow-up command `triage-review-promote <id> --confirm`.
   Collapse `applied`-with-no-reply to a count unless asked for `--all` detail.

## Notes

- `pipeline-summary` is pure-read; it's safe to run anytime (e.g. "where do my
  apps stand") without doing the email fetch — just run step 6 alone.
- The pipeline reads the analytics tracking model (`{lead_id}-status.json`). If
  it's empty, leads haven't been advanced through `triage`/`tracking` yet — the
  roster fills in as replies are triaged.
