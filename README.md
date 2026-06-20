# Job Hunt

`job-hunt` is an agent-first, file-backed repository for discovering, scoring, reviewing, and optionally applying to jobs on behalf of one person.

The repo is designed for Claude Code style operation:
- user profile documents live in `profile/raw/`
- normalized artifacts live in `profile/normalized/`
- discovered jobs live in `data/leads/`
- application drafts and reports live in `data/applications/` and `docs/reports/`
- runtime behavior is controlled by `config/*.yaml`

## Safety Defaults

V1 is optimized for trust, not volume.

- **The agent fills application forms but never clicks the final Submit button — the human always does** (Batch 4 v4 invariant; see `docs/guides/indeed-auto-apply.md`)
- a separate approval gate is required before account creation
- speculative facts are disabled by default
- browser execution has a soft limit of 10 tabs and a hard limit of 15
- credentials must never be written to git-tracked artifacts
- LinkedIn URLs hard-fail; Indeed.com is the only allowlisted login-walled domain (per `config/domain-allowlist.yaml`)

## Repository Layout

```text
job-hunt/
├── AGENTS.md
├── config/
├── data/
├── docs/
├── playbooks/
├── profile/
├── prompts/
├── schemas/
├── scripts/
├── src/job_hunt/
└── tests/
```

## Quick Start

1. Add candidate materials to `profile/raw/`
2. Normalize them:

```bash
python3 scripts/job_hunt.py normalize-profile
```

This now produces:
- `profile/normalized/candidate-profile.json`
- `profile/normalized/document-audit.json`
- `profile/normalized/documents/*.json`
- `docs/reports/profile-document-audit.md`

If you only want to rerun the intake/audit pass, use:

```bash
python3 scripts/job_hunt.py audit-profile-docs
```

3. Discover jobs from target companies:

```bash
cp config/watchlist.example.yaml config/watchlist.yaml
# edit config/watchlist.yaml — add your target-company source fields
python3 scripts/job_hunt.py discover-jobs
```

`config/watchlist.yaml` is gitignored (target-company names are
PII-adjacent). See `docs/guides/job-discovery.md` for filter semantics,
cursor behavior, USAJOBS local credential setup, and review-queue triage.

4. Add or collect a raw job description file, then extract and score it:

```bash
python3 scripts/job_hunt.py extract-lead --input examples/leads/senior-platform-engineer.md
python3 scripts/job_hunt.py score-lead --lead data/leads/<lead-id>.json
```

4. Build a reviewable draft:

```bash
python3 scripts/job_hunt.py build-draft --lead data/leads/<lead-id>.json
```

5. After a browser run, write the final report:

```bash
python3 scripts/job_hunt.py write-report \
  --draft data/applications/<draft-id>.json \
  --attempt examples/results/application-attempt.json
```

6. Summarize the run:

```bash
python3 scripts/job_hunt.py summarize-run
```

Generated reports now capture:
- approval-required vs approval-obtained for both account creation and final submit
- submit attempted vs confirmed submitted
- browser tab budget metrics and hard-limit breaches
- redaction status for any secret-like fields present in runtime attempt data

## Level 1.5 Co-Pilot (scan → score → route → packet → handoff)

A safe, fully-logged chain that takes "find new SWE jobs from the last hour that
match my resume, pick the best variant, and prepare the application up to — but
not including — submit." Every decision is a concrete artifact. See
[`docs/ai/architecture-copilot-level-1.5.md`](docs/ai/architecture-copilot-level-1.5.md).

```bash
# 1. Recent-job scan: leads inside a wall-clock window, grouped by fit tier
python3 scripts/job_hunt.py scan-recent-jobs --since 1h

# 2. Route a scored lead to its best resume variant (writes a logged decision)
python3 scripts/job_hunt.py select-resume-variant --lead data/leads/<id>.json

# 3. Plan the whole run as one dry-run with a per-job decision log (never submits)
python3 scripts/job_hunt.py copilot-run --since 1h --min-tier maybe
```

Authoring real profile/resume content? See
[`docs/guides/profile-and-resume-privacy.md`](docs/guides/profile-and-resume-privacy.md)
for what is safe to track vs. kept private, the per-lane authoring templates
(`profile/resumes/templates/`), and the claims truth bank
(`profile/claims/`). Validate the foundation with:

```bash
python3 scripts/job_hunt.py profile-doctor
```

Resume variants are a **config-driven registry** (`config/resume-variants.json`):
job-title lanes map to pre-authored resume files. Routing scores each lane by
title-pattern match + emphasis-skills overlap + seniority, picks the best, and
flags `needs_human_review` when the resume file is missing, two lanes are near a
tie, confidence is low, or the lead was never scored. Drop your variants under
`profile/resumes/` (see `profile/resumes/README.md`). `copilot-run` stops at the
human submit gate by construction — it prepares, it never submits.

### Scheduled review (cron / launchd friendly)

`run-scheduled-review` wraps the whole safe chain into one repeatable command for
"jobs from the last X hours": scoped public discovery → readiness ranking →
prepare up to a hard-capped number of packets (PDFs auto-exported) → read-only
packet review → a safe aggregate summary. It never applies, opens a browser,
fills a form, creates an account, or submits — generated packets are
human-submit only.

```bash
# Typical scheduled run: last 12h, generate at most 2 packets
python3 scripts/job_hunt.py run-scheduled-review --hours 12 --max-packets 2

# Review-only (no generation), offline over already-stored leads
python3 scripts/job_hunt.py run-scheduled-review --hours 6 --max-packets 0 --no-discover
```

Built-in guardrails (each can `warn` or `fail` the run): `--max-packets` is a
hard ceiling on generation per run (0 disables it); `profile-doctor` must be
clean (`--strict-doctor` upgrades warnings to a failure); every private path
must be gitignored; and PDF export of generated packets must succeed
(`--strict-pdf`). A failing guardrail returns a non-zero exit so a scheduler can
gate on it. Private preferences are summarized as counts/booleans only; no
resume/cover-letter prose is ever printed.

A suggested daily schedule (review only, generate by hand) — not installed for
you:

```cron
# 8:00am local, weekdays — last 24h, prepare up to 2 packets, log safely
0 8 * * 1-5  cd /path/to/job-hunt && SSL_CERT_FILE="$(python3 -m certifi)" \
  python3 scripts/job_hunt.py run-scheduled-review --hours 24 --max-packets 2 \
  >> data/watch/scheduled-review.log 2>&1
```

### Manual packet lifecycle (after you review/submit by hand)

Once you have personally reviewed a packet and (optionally) submitted it through
the company's own site, record what happened with `mark-packet`. It updates only
the local, gitignored packet `status.json` — it never submits, opens a browser,
fills a form, or touches an account, and it never flips the `requires_human_submit`
invariant.

```bash
# Record that YOU submitted it (URL is stored, never opened)
python3 scripts/job_hunt.py mark-packet --draft-id <draft-id> \
  --status manually_submitted --submitted-url https://company.com/careers/123

# Park, skip, or flag for rework
python3 scripts/job_hunt.py mark-packet --draft-id <draft-id> --status follow_up_later \
  --follow-up-date 2026-07-01
python3 scripts/job_hunt.py mark-packet --draft-id <draft-id> --status skipped
python3 scripts/job_hunt.py mark-packet --draft-id <draft-id> --status needs_revision

# Preview a transition without writing anything
python3 scripts/job_hunt.py mark-packet --draft-id <draft-id> --status reviewed --dry-run
```

Supported dispositions: `reviewed`, `manually_submitted`, `skipped`,
`not_interested`, `needs_revision`, `follow_up_later`, `interviewing`, `rejected`
(`rejected` is terminal). Illegal transitions and unknown draft ids are rejected
with a clear error. Packets you have submitted / skipped / closed drop out of the
`packets-review` and `run-scheduled-review` action queues automatically.

Inspect a single packet's timeline (read-only, never prints private prose):

```bash
python3 scripts/job_hunt.py packet-history --draft-id <draft-id>
```

### Per-packet manual submission checklist

Every prepared packet folder gets a `MANUAL_SUBMISSION.md` checklist so the
folder is self-contained for finishing the application by hand. It gathers only
safe completion metadata — company / title / posting URL, draft + lead id,
resume lane, score / tier / fit, the resume and cover-letter PDF paths (markdown
fallback when a PDF is unavailable), the ATS warning/error counts, the claims
approval count (e.g. `3/3 approved`, never claim text), claim-safety flags, a
short manual review checklist, the exact `mark-packet` post-action commands, and
a human-submit safety notice. It never contains resume / cover-letter prose or
claim text. The file is generated automatically when a packet is prepared (and
is gitignored along with the rest of the packet).

Backfill the checklist into packets prepared before this existed (writes only
the local, gitignored checklist files — never submits, opens a browser, or
prints private prose):

```bash
python3 scripts/job_hunt.py refresh-packet-checklists
```

`packets-review` and `packet-history` report whether each packet's
`MANUAL_SUBMISSION.md` is present.

## Batch 4: Autonomous Indeed Apply

End-to-end pipeline for applying to Indeed postings: lead discovery →
prepare-application (form-field plan + tailored resume + ATS check) →
agent drives Chrome via the Claude-in-Chrome MCP → **human clicks Submit**
→ Gmail-driven confirmation closes the lifecycle. The user-facing guide
lives at [`docs/guides/indeed-auto-apply.md`](docs/guides/indeed-auto-apply.md).

The v4 policy invariant: the agent never clicks Submit. Tiers describe
how much field-level review the human does before clicking, not whether
the click happens. See `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
for the design rationale.

## Discovery Sources

Active discovery now supports:

- `greenhouse`
- `lever`
- `careers`
- `indeed_search`
- `ashby`
- `workable`
- `usajobs`

The discovery/source catalog is tracked in `config/sources.yaml` and is
kept in lockstep with the runtime provider registry. `watchlist-validate`
also reports USAJOBS readiness states (`profile_missing`,
`credentials_missing`, `ready`) so missing local setup is visible before a
run fails.

## Batch 2: URL Ingestion, PDF Export, ATS Checks, Analytics

Once the pipeline is running, batch 2 adds:

### URL-based lead ingestion

Paste a Greenhouse or Lever URL — fetch via their public JSON APIs, canonicalize
away tracking params, and produce a lead:

```bash
python3 scripts/job_hunt.py ingest-url --url https://boards.greenhouse.io/exampleco/jobs/12345

# Batch from a file (one URL per line, parallelized)
python3 scripts/job_hunt.py ingest-url --urls-file inbox.txt

# Offline/login-walled sites — pre-downloaded HTML bypasses the network
python3 scripts/job_hunt.py ingest-url --url https://careers.example.com/j/1 --html-file saved.html
```

LinkedIn and Indeed are hard-failed with a `login_wall` error code. Private IPs
and non-http(s) schemes are blocked. Response bodies are capped (2MB raw,
5MB decompressed) to guard against resource exhaustion and compression bombs.

### PDF export

Generated markdown resumes and cover letters can be rendered to ATS-friendly
PDFs on demand. PDF support requires the optional extra:

```bash
pip install 'job-hunt[pdf]'

# macOS: also needs native deps
brew install pango cairo gdk-pixbuf libffi

# Linux:
apt install libpango-1.0-0 libpangoft2-1.0-0
```

On macOS (esp. Apple Silicon with the python.org framework Python), WeasyPrint
imports but cannot find the Homebrew native libraries at runtime unless the
loader path is exported — symptom: "WeasyPrint could not import some external
libraries". Prefix `export-pdf` with the Homebrew lib path:

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  python3 scripts/job_hunt.py export-pdf --content-id <content-id>
```

```bash
# Primary: path-based (matches batch 1 convention)
python3 scripts/job_hunt.py export-pdf \
  --content-record data/generated/resumes/<content-id>.json

# Convenience: id-based
python3 scripts/job_hunt.py export-pdf --content-id <content-id>
```

The core pipeline works without weasyprint; only `export-pdf` needs it.

### ATS compatibility checking

Runs automatically at the end of `generate-resume` and `generate-cover-letter`
(opt-out via `--skip-ats-check`). Can also be run standalone:

```bash
python3 scripts/job_hunt.py ats-check \
  --content-record data/generated/resumes/<id>.json \
  --lead data/leads/<lead-id>.json \
  --target-pages 1
```

Checks: required sections (Technical Skills, Professional Experience,
Education), length (≤1 page default for <5 YOE, 475-600 word target),
keyword coverage (≥60% of lead keywords in content), and keyword density
(flag >5% as stuffing). Never blocks generation — produces a report with
error/warning status that `check-integrity` surfaces for re-run.

### Pipeline analytics

```bash
# Application velocity dashboard — weekly counts, callback rate, variant win rates
python3 scripts/job_hunt.py apps-dashboard [--since 2026-04-01] [--weeks 8]

# Skills gap analysis — required skills missing from your profile
python3 scripts/job_hunt.py analyze-skills-gap

# Rejection pattern analysis — stage drop-offs, industry patterns, missing-skill correlations
python3 scripts/job_hunt.py analyze-rejections

# Learning loop — propose scoring.yaml + profile-evidence changes from outcomes.
# Writes a reviewable proposal to data/calibration/; NEVER edits scoring.yaml.
python3 scripts/job_hunt.py calibrate-scoring
```

`calibrate-scoring` closes the loop between outcomes and selection criteria.
It is propose-only by design (see AGENTS.md): a human reads the proposal and
edits `config/scoring.yaml` by hand. Proposals are sample-size gated — no
evidence, no proposal.

### Inbound email → status triage (feeds the learning loop)

`calibrate-scoring` is only as good as the outcomes recorded in the ledger.
Triage classifies inbound recruiter/ATS email and advances the matched
lead's status automatically, so the loop is fed without manual
`update-status`:

```bash
# Print the Gmail search query the agent should fetch with (no fetch here)
python3 scripts/job_hunt.py triage-inbox --emit-query --window-days 14

# Classify a fetched batch (JSON list of Gmail messages) — dry-run first
python3 scripts/job_hunt.py triage-inbox --inbox-file inbox.json --dry-run
python3 scripts/job_hunt.py triage-inbox --inbox-file inbox.json

# Time-based: mark stale non-terminal leads as ghosted
python3 scripts/job_hunt.py triage-ghosts --days 21 --dry-run

# Resolve the quarantine: list, propose, then apply under --confirm
python3 scripts/job_hunt.py triage-review-list
python3 scripts/job_hunt.py triage-review-promote <message_id>            # propose only
python3 scripts/job_hunt.py triage-review-promote <message_id> --confirm  # apply + GC
python3 scripts/job_hunt.py triage-review-dismiss <message_id> --reason "recruiter spam"
```

Triage is verification-bound and anti-spoof (see AGENTS.md): outcomes from
non-allowlisted senders quarantine to `data/applications/_suspicious/` for
human review rather than auto-applying. The `triage-review-*` triad
re-derives a `{lead_id, stage}` proposal from the quarantined message and
applies it only under explicit `--confirm` (`--lead`/`--stage` override when
re-derivation is not confident); resolving an entry GCs its file. The
operator/agent runbook is
[`docs/guides/inbound-email-triage.md`](docs/guides/inbound-email-triage.md).

All three reports use sample-size gates: `confidence: insufficient_data`
(<10 items), `low` (10-29), `ok` (30+). Ingest more leads before trusting
rates.

## Testing

Run the standard-library test suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Repo Privacy

This repository should be private if it contains real candidate materials, job application records, or company-specific notes.
