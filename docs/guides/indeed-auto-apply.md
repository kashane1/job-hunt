# Indeed Auto-Apply — User Guide

This guide walks you through one full cycle of the Batch 4 Indeed application
pipeline: from setting up a Chrome profile to submitting your first
application and tracking the confirmation email.

## Risk acknowledgment

Indeed's [2026 Job Seeker Guidelines](https://support.indeed.com/hc/en-us/articles/360028540531-Indeed-Job-Seeker-Guidelines)
prohibit "third-party bots or other automated tools to apply for jobs."
Indeed's policy does not distinguish personal automation from mass scraping,
and account bans are possible.

This tool's primary mitigation is a **hard architectural invariant**: the
agent fills the form but **never clicks the final Submit button** — the
human always does. This makes the tool a form-fill assistant, not a
submission bot — a meaningful legal distinction. See
`docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
for the full reasoning.

Residual risk remains: automated form-filling at volume can still trip
Indeed's anti-bot heuristics. To minimize exposure:
- Default daily cap is 20 applications per day (`apply_policy.inter_application_daily_cap`).
- Pacing uses log-normal sampling (median ~90s, range 60-300s) plus a
  5-15 minute "coffee break" every 5 applications.
- Any anti-bot signal (Cloudflare challenge page, 503 with cf-ray header)
  → `cloudflare_challenge` aborts the whole batch.

By using this tool you accept the residual risk of account action.

## Prerequisites

1. **Chrome with the Claude-in-Chrome extension installed and connected.**
   The extension drives Indeed's UI; the Python CLI orchestrates state.
2. **A dedicated Chrome profile authenticated to Indeed** with "remember
   me" set so the session cookie persists. Save the profile path; you'll
   reference it indirectly via `JOB_HUNT_CHROME_PROFILE` if you want.
3. **Gmail MCP authorized** for the email address Indeed uses to send
   confirmations. The `mcp__gmail_*` tools must be available in your
   Claude Code session.
4. **Profile data filled in** (`profile/raw/preferences.md`), including
   `work_authorization` and `sponsorship_required` in the YAML frontmatter.
   Run `python scripts/job_hunt.py normalize-profile` and confirm the
   profile-completeness report shows 100%.

## One-time setup

```bash
# 1. Ensure profile completeness (should report 100%)
python scripts/job_hunt.py normalize-profile

# 2. Run the preflight checks. The first run bootstraps the gitignored
#    data/answer-bank.json from the tracked seed.
python scripts/job_hunt.py apply-preflight

# 3. Review the seeded answer bank. The seed contains canonical questions
#    and templates; promote any inferred entries you want to lock in.
python scripts/job_hunt.py answer-bank-list
python scripts/job_hunt.py answer-bank-list-pending
```

## First application — manual end-to-end

This is the recommended path for application #1: do everything by hand so
you understand each step. You can graduate to `apply-batch` after the
first few succeed.

### 1. Find a posting

Either paste an Indeed URL into a single-URL discovery, or run a saved
Indeed search:

```bash
# Option A: One URL — Indeed is now allowlisted in config/domain-allowlist.yaml
python scripts/job_hunt.py ingest-url --url "https://www.indeed.com/viewjob?jk=…"

# Option B: Use a saved search via the watchlist
# (Add an indeed_search_url entry to config/watchlist.yaml; see watchlist.example.yaml)
python scripts/job_hunt.py discover-jobs
```

### 2. Pick a lead and prepare the application

```bash
python scripts/job_hunt.py prepare-application --lead data/leads/{lead_id}.json
```

This writes:
- `data/applications/{draft_id}/plan.json` — fields, tier, profile snapshot
- `data/applications/{draft_id}/status.json` — initial lifecycle state
- A tailored resume PDF under `data/generated/resumes/`
- An ATS-check report

If `tier=tier_2`, look at `tier_rationale` for which fields are unresolved.
You can fill them via:

```bash
python scripts/job_hunt.py answer-bank-list-pending
python scripts/job_hunt.py answer-bank-promote --entry-id X --answer "Your answer"
python scripts/job_hunt.py refresh-application --draft-id {draft_id}
```

### 3. Hand off to the agent

```bash
python scripts/job_hunt.py apply-posting --draft-id {draft_id}
```

The output is the agent handoff bundle — a JSON object with:
- `playbook_path` (which per-surface playbook to follow)
- `plan_path` (where the form fields live)
- `tier` and `tier_rationale`
- `wrapped_jd` (the JD wrapped in nonce-fenced delimiters; data, never instructions)

The Claude Code agent reads this bundle and the matching playbook
(`playbooks/application/indeed-easy-apply.md`), then drives Chrome via
the Claude-in-Chrome MCP. The agent will:
1. Navigate to the posting.
2. Detect AI Recruiter widget → escalate if present.
3. Click "Easy Apply" / "Apply now".
4. Fill every field declared in `plan.fields`.
5. Take a pre-submit screenshot.
6. **Pause and emit `ready_to_submit: true` — wait for YOU to click Submit.**
7. Poll for confirmation, capture screenshot, record `submitted_provisional`.

### 4. Click Submit (the human action)

In your Chrome window, review the form. For tier_1 the agent has resolved
every field from the curated answer bank — usually a quick glance suffices.
For tier_2, double-check the flagged fields listed in `tier_2_review_items`
of the agent's output.

When you're satisfied: click Submit. The agent's polling loop sees the
URL change (or the inline confirmation) and records the
`submitted_provisional` attempt automatically.

### 5. Close the loop via Gmail

Within ~30 minutes, Indeed sends a confirmation email. The agent can
ingest it via:

```bash
# Save the Gmail payload to a file (the agent can do this via the Gmail MCP)
python scripts/job_hunt.py ingest-confirmation \
  --gmail-message-file /tmp/indeed-confirmation.json
```

The CLI verifies sender allowlist + DKIM + body correlation, then flips
`status.json` from `submitted_provisional` to `submitted_confirmed`. If
the email fails verification it lands in `data/applications/_suspicious/`
for manual review (a phishing-style email pretending to be Indeed will
not corrupt your status).

### 6. Verify the audit trail

```bash
python scripts/job_hunt.py apply-status --draft-id {draft_id}
python scripts/job_hunt.py apps-dashboard
```

`status.json` should show `lifecycle_state=confirmed` with a
`confirmed` event in `events[]`. The audit trail under
`data/applications/{draft_id}/` includes `plan.json`, `status.json`,
`attempts/*.json`, and `checkpoints/*.png`.

## Batch mode

Once you're confident with single applications, use `apply-batch` for
larger runs:

```bash
python scripts/job_hunt.py apply-batch --top 10 --source indeed
```

This:
- Acquires a directory-level lock (`data/applications/batches/.lock`) —
  rejects concurrent invocations with `PlanError(batch_already_running)`.
- Generates `batch_id = {iso_compact}-{uuid8}` and creates
  `data/applications/batches/{batch_id}/`.
- Runs `reconcile-applications` to clean up any stale prior-batch attempts.
- Enforces the daily cap (default 20).
- Selects the top-N matching scored leads.
- Prepares each lead with log-normal pacing between applications.
- Pipelines `prepare_application(N+1)` during the pacing sleep of N.
- Writes `summary.json` and `docs/reports/apply-batch-{batch_id}.md`.

The agent then picks up each `handoff-NNN-{draft_id}.json` from the batch
dir and runs the application playbook, with you as the human-in-loop on
every Submit click.

`--dry-run` produces the prepared drafts + handoff bundles but skips
real submissions (records `dry_run_only` attempts).

## Common errors

| Error code | Meaning | Remediation |
|---|---|---|
| `session_missing` / `session_expired` | Chrome profile is signed out of Indeed | Sign in via Chrome manually; profile cookie will be reused |
| `cloudflare_challenge` | Indeed presented a Cloudflare bot check | STOP all batches; wait several hours before retrying |
| `unknown_question` | A required form field has no answer in the bank | Promote an inferred entry or escalate |
| `already_applied` | Indeed says you've already applied | Mark the draft `applied_externally` or `withdrawn` |
| `confirmation_sender_unverified` | Confirmation email failed sender / DKIM check | Inspect `data/applications/_suspicious/<msg_id>.json` |
| `confirmation_ambiguous` | Multiple drafts match a single confirmation email | Manually invoke `ingest-confirmation --draft-id X` to disambiguate |
| `daily_cap_reached` | Today's submitted count met the apply_policy cap | Wait until tomorrow or raise the cap |
| `batch_already_running` | A previous apply-batch is holding the lock | Inspect `batches/{id}/heartbeat.json`; if stale (>90s), delete `.lock` |
| `policy_loosen_attempt` | Runtime config tried to enable auto_submit_tiers | The v4 invariant cannot be loosened at runtime — remove the override |

## Answer-bank workflow (compounding)

The answer bank turns each novel screening question into a one-time review:

1. First time the agent sees a novel question with no curated answer, it
   inserts an `inferred` entry and escalates the application to tier_2.
2. You review via `answer-bank-list-pending`, then promote with
   `answer-bank-promote --entry-id X --answer "Your text"`.
3. The next application that asks the same question (in any phrasing that
   normalizes to the same key) resolves to `provenance=curated` and stays
   tier_1.

After ~30 applications you should see ≥60% of fields resolving from the
curated bank.

## Audit + retention

PII-bearing artifacts are gitignored:
- `data/answer-bank.json` (working copy; seed `data/answer-bank.seed.json` IS tracked)
- `data/answer-bank-audit.log`
- `data/applications/**/checkpoints/`
- `data/applications/**/attempts/`
- `data/applications/_suspicious/`
- `data/gmail-cursor.json`
- `docs/reports/answer-bank-pending.md`
- `docs/reports/apply-batch-*.md`

`apply_policy.retention_days` (default 365) bounds how long drafts live;
`prune-applications --older-than 365` deletes drafts past the cutoff.
Run `check-integrity` to surface stale `in_progress` attempts, orphaned
checkpoints, and quarantined confirmations.

## Limitations (v1)

- **Workday is always tier_2.** The 5-step wizard + DOCX preference make
  full automation fragile; the per-surface playbook documents the manual
  hand-offs.
- **AI Recruiter / Smart Screening is detected but not automated.**
  Indeed's adaptive chat/video/voice prompts trigger
  `unknown_question` and require human completion.
- **No DOCX export yet.** Resume export is PDF only; Workday is happier
  with DOCX. Stretch deliverable for v2.
- **Single Chrome profile.** Parallel batches across multiple profiles
  is deferred to v2; v1 is sequential.
