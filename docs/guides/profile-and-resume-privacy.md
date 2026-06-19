# Profile & Resume Privacy / Git Policy

How real profile, resume, and claims data is kept private while the *structure*
around it stays tracked and shareable. Enforced by `.gitignore` and verified by
`profile-doctor`.

## Principle

Track the **scaffolding** (schemas, config, templates, sanitized examples,
READMEs, code). Never track the **content** (your real resume text, normalized
profile, claims, leads, application packets, generated PDFs, decision logs).
Even with a private repo, treat real materials as sensitive — a repo can become
public, be forked, or be shared.

## What is tracked vs. private

| Path | Tracked? | Why |
|------|----------|-----|
| `config/resume-variants.json` | ✅ tracked | Lane registry — generic, no PII |
| `schemas/*.json` | ✅ tracked | Contracts |
| `profile/resumes/templates/*.template.md` | ✅ tracked | Empty authoring scaffolds |
| `profile/resumes/README.md`, `profile/claims/README.md` | ✅ tracked | Docs |
| `profile/claims/claims-bank.example.json` | ✅ tracked | **Sanitized, fictional** example |
| `examples/profile/`, `examples/copilot/` | ✅ tracked | Sanitized examples |
| `profile/resumes/*.md` (real lane files) | 🔒 gitignored | Real resume content |
| `profile/claims/claims-bank.json` | 🔒 gitignored | Real claims |
| `profile/raw/`, `profile/normalized/` | 🔒 gitignored | Raw + normalized profile |
| `data/generated/`, `data/applications/`, `data/leads/`, `data/runs/`, `data/calibration/`, `data/discovery/`, `data/companies/` | 🔒 gitignored | Runtime PII artifacts |
| generated PDFs (`data/generated/...`) | 🔒 gitignored | Rendered resumes/letters |
| decision logs (`data/runs/copilot-*/`) | 🔒 gitignored | Embed scored lead data |
| `docs/reports/*-report.md`, `docs/reports/*-audit-*.md` | 🔒 gitignored | Generated/candid reports |

### Explicit decisions

- **Generated PDFs** → private (under `data/generated/`).
- **Normalized profile JSON** → private (`profile/normalized/`).
- **Application packets** → private (`data/applications/`).
- **Copilot decision logs** → private (`data/runs/`). Share only a *sanitized*
  copy under `examples/copilot/` (as done for the Level 1.5 package).
- **Capability/audit reports** → private (`docs/reports/*-audit-*.md`). They
  carry candid personal job-search context.

## Creating a sanitized example

1. Copy the real artifact.
2. Replace name, email, phone, and employer/company specifics with placeholders.
3. Remove or generalize any metric you cannot publish (revenue, headcount, %).
4. Mark it clearly (`*.example.*`, `[EXAMPLE]` text, an `_comment` field).
5. Run a PII grep before committing:
   `grep -rniE "your-name|your-email|@gmail|[0-9]{3}[-.][0-9]{3}[-.][0-9]{4}" <file>`
6. Confirm it is not under a gitignored path (or it cannot be tracked).

## Lane title taxonomy → registry lanes

The broader title taxonomy maps onto the four registry lanes:

| Title family | Lane |
|--------------|------|
| backend / platform / infra / SRE | `platform_backend` |
| fullstack / product / frontend / React | `fullstack_product` |
| data / automation / AI / agentic / ML | `ai_engineer` |
| engineering productivity / internal tools | `platform_backend` (or `fullstack_product` if product-facing) |
| anything else | `generalist_swe` (default/fallback) |

Add a new registry lane only when a title family needs a materially different
resume; otherwise route it to the closest existing lane to avoid drift.

## Verifying

```bash
python3 scripts/job_hunt.py profile-doctor
```

Flags: lanes marked `ready` without a resume file or approved claims; missing
templates; an unresolved default lane; and — importantly — **any private/PII
path that is currently tracked by git**.

### If `profile-doctor` reports `private_tracked`

A file under a private path is tracked (often committed before the ignore rule
existed; `.gitignore` does not untrack). To stop tracking it while keeping the
local file:

```bash
git rm --cached <path>      # keeps the file on disk, removes it from the index
git commit -m "chore(privacy): stop tracking <path>"
```

Note this does **not** remove the file from past history. If the content is
sensitive and the repo was ever shared/public, scrub history separately
(`git filter-repo`) and rotate anything exposed.
