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

3. Add or collect a raw job description file, then extract and score it:

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

## Testing

Run the standard-library test suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Repo Privacy

This repository should be private if it contains real candidate materials, job application records, or company-specific notes.
