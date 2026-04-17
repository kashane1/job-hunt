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

- human approval is required before every final submit
- a separate approval gate is required before account creation in v1
- speculative facts are disabled by default
- browser execution has a soft limit of 10 tabs and a hard limit of 15
- credentials must never be written to git-tracked artifacts

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
# edit config/watchlist.yaml — add your target-company Greenhouse/Lever slugs
python3 scripts/job_hunt.py discover-jobs
```

`config/watchlist.yaml` is gitignored (target-company names are
PII-adjacent). See `docs/guides/job-discovery.md` for filter semantics,
cursor behavior, and review-queue triage.

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
```

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
