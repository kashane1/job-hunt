# job-hunt — session handoff (2026-05-17)

Read this first when resuming on a new machine.

## What this repo is

Kashane's automated job-hunt system: discovery → scoring → application
drafting/answering, file-backed, with approval gates. First job search in
10+ years.

## What the recent session focused on

Making the **ai-company-os** project (cited as the proudest side project in
applications) describable **truthfully and scrutiny-proof**. An unbiased
review found the application answer claimed "built over the last year" and
"running for months in production" — both falsified by `git log` in 30
seconds (that repo is ~2 months old). Fixed at the source.

## What was done (committed: `3afc5cd`, pushed to origin/main)

- Rewrote `profile/raw/ai-company-os.md` — honest AI-first thesis framing,
  concrete verifiable facts, explicit "do not inflate timeline" instruction
  so generated answers stay strong **and** unfalsifiable.
- Rewrote the ai-company-os Q/A in `profile/raw/accomplishments.md`
  (line ~49, "safe agent orchestration") to the same true framing.
- Corrected a schema overclaim: domain schemas are **frozen dataclasses
  with enum-constrained fields**; Pydantic only guards the API surface
  (not "typed with Pydantic schemas").
- Added an anti-inflation hard rule to
  `prompts/answering/application-answering.md` (root-cause fix: the false
  timeline was invented at answer-generation time, not stored).
- `profile/raw/` is **gitignored**; the two files above were `git add -f`'d
  so they now travel with the repo. Other `profile/raw/` files remain
  local-only.

## How to resume

1. After ANY edit to `profile/raw/*`, regenerate the normalized profile:
   ```bash
   python3 scripts/job_hunt.py normalize-profile
   ```
2. Verify no banned phrases leaked into generated answers:
   ```bash
   grep -ri "over the last year\|months in production\|running for months" profile/normalized/ || echo clean
   ```
   (`profile/normalized/` is gitignored — it is a build artifact, never
   committed.)

## Next goals

- Resume normal job-hunt operation (discovery/apply) via the `/apply-mode`
  and `/job-title-ledger` skills — those carry the operational playbooks.
- If any application answer still points at ai-company-os, ensure it uses
  the new framing (regenerate first).
- Optional: tighten a slightly clunky double-"with" phrase in the
  `accomplishments.md` ai-company-os answer.

## Companion

The ai-company-os repo has its own `HANDOFF.md` covering the open PR #48
and its next goals. The two workstreams are linked: this repo's answers
describe that repo.
