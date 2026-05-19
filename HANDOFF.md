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

## Repo health (2026-05-18)

The test suite is **green**: `548 tests, OK`. Verify before any work:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

A 2026-05-18 audit found the suite had been left red (2 failures + 1
error) — none were production bugs, but a red suite hides real
regressions in a trust-first system. Fixed in this session (P0):

- **Mapped-IPv6 SSRF spec.** `_ip_is_disallowed` now rejects the entire
  `::ffff:0:0/96` class explicitly and first — fail-closed and
  deterministic across CPython versions (was relying on version-dependent
  `is_reserved`/`is_private`, and the explicit branch was dead code). See
  the 2026-05-18 update in
  `docs/solutions/security-issues/pin-validated-ip-to-close-dns-rebinding-and-mapped-ipv6-ssrf.md`.
- **Anti-bot jitter** is now a single source of truth
  (`discovery.install_anti_bot_jitter` + `ANTI_BOT_JITTER_*`); the test
  asserts limiter behavior/envelope, not source text (was a brittle
  source-string match that broke on a 25→30 retune).
- **Two networked tests** (`test_discovery` LinkedIn allowlist,
  `test_ingestion` LinkedIn/Indeed allowlist) are now hermetic — `fetch`
  is mocked so they assert the allowlist-gate contract deterministically
  instead of relying on a real network call.

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

Audit roadmap (2026-05-18) — P0 done, working down P1 → P2:

- **P1 (in progress this session):** screenshot PII sanitizer (todo 045,
  decided/ready); redact `_intake/failed/` content+URL leak (todo 027,
  re-ranked P3→P1 — it's a privacy leak on disk).
- **P2:** close the learning loop with a human-approved
  `calibrate-scoring` (analytics → proposed `scoring.yaml`/answer-bank
  deltas, never auto-applied); prepare the Indeed MCP/form spike harness
  (todo 046 — live execution requires Chrome + Indeed account).
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
