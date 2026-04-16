---
status: pending
priority: p2
issue_id: "023"
tags: [code-review, pattern, cli, batch-2]
dependencies: []
---

# CLI uses --content-id but batch 1 convention is --content-record PATH

## Problem Statement

Pattern review found the plan's `--content-id <id>` flag on `export-pdf` and `ats-check` breaks batch 1's convention where lookup commands take file paths (`--lead PATH`, `--company PATH`, `--draft PATH`).

Separately: `ingest-url <url>` uses a positional argument, but batch 1 uses named flags only (`--input`, `--lead`, etc.).

## Findings

### Inconsistency 1: --content-id vs --content-record PATH

Batch 1 pattern:
- `update-status --lead data/leads/{lead-id}.json`
- `generate-resume --lead data/leads/{lead-id}.json`
- `score-company-fit --company data/companies/{company-id}.json`
- `check-status --lead PATH`
- `write-report --draft PATH --attempt PATH`

All lookup by file path. Consumer (human or agent) sees a direct filesystem path; no hidden id-to-path resolution.

Plan introduces:
- `export-pdf --content-id <id>`
- `ats-check --content-id <id>`

An agent invoking these must know the id-to-path mapping (`data/generated/resumes/{id}.json`). Resolution logic becomes hidden inside the CLI.

### Inconsistency 2: positional vs named for ingest-url

Batch 1 every command uses named flags:
- `extract-lead --input PATH`

Plan:
- `ingest-url <url>` — positional

## Proposed Solutions

### Option 1: Align to batch 1 convention (Recommended)

**Primary: use `--content-record PATH`:**
```bash
export-pdf --content-record data/generated/resumes/senior-platform-exampleco-impact_focused-20260416T143022.json
ats-check --content-record data/generated/resumes/{id}.json
```

**Optional convenience: also accept `--content-id ID`** (resolves against a default generated dir):
```bash
export-pdf --content-id <id>   # looks in data/generated/{resumes,cover-letters,answers}/
```

The CLI would accept either flag (mutually exclusive), path-based primary for pattern consistency.

**ingest-url: use named flag `--url`:**
```bash
ingest-url --url https://boards.greenhouse.io/co/jobs/123
ingest-url --urls-file inbox.txt
ingest-url --url URL --html-file saved.html
```

**Effort:** Small
**Risk:** Low

### Option 2: Accept the deviation as intentional

The user case (batch-1 UX is path-heavy, batch-2 focuses on ID references) might argue for an evolution. If so, document it explicitly and retrofit batch 1 to also accept `--X-id` variants for consistency going forward.

Costs more work; doesn't scale if every batch introduces a new CLI convention.

## Recommended Action

Option 1. Path-first matches batch 1 and is what existing agent scripts will already expect. `--content-id` can be a secondary convenience.

## Acceptance Criteria

- [ ] `export-pdf --content-record PATH` is the primary flag
- [ ] `ats-check --content-record PATH` is the primary flag
- [ ] `ingest-url --url URL` uses named flag, not positional
- [ ] `--content-id ID` may remain as mutually-exclusive alternative
- [ ] AGENTS.md examples updated to match

## Work Log

### 2026-04-16 - Discovery

**By:** pattern-recognition-specialist

**Actions:**
- Identified path-first convention across batch 1 lookup commands
- Noted named-flag convention for all batch 1 CLI args
