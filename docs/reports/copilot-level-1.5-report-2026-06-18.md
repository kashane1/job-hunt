# Level 1.5 Co-Pilot — Build Report (2026-06-18)

## What was asked

Turn the repo into a safe job-application co-pilot working toward:

> "Find any new software-engineering jobs posted in the last hour that strongly
> match my resume, choose the best resume variant, generate tailored materials,
> and prepare the application on the company site up to — but not including —
> final submit."

Constraints: no auto-submit; final submit stays human; prioritize company sites
+ Greenhouse/Lever/Ashby/Workable; multiple resume variants with automatic
routing; log every decision; concrete CLI/artifacts/tests, no "AI magic".

## What already existed (and was reused, not rebuilt)

The repo is mature (~18k LOC, 612 tests at baseline). Already present and kept:
`score-lead` (fit scoring + tier thresholds in `config/scoring.yaml`),
`prepare-application` (per-job packet folder), `generate-resume/cover-letter/
answers`, `apply-posting` (browser handoff bundle + per-surface playbooks),
discovery for Greenhouse/Lever/Ashby/Workable/careers/USAJOBS/Indeed, and the
**hard compile-time human-submit invariant** (`auto_submit_tiers = []`).

## What was added (the Level 1.5 delta)

| Piece | Where |
|-------|-------|
| Config-driven resume variant **registry** | `config/resume-variants.json` + `schemas/resume-variant-registry.schema.json` |
| Title→variant **routing** with logged decision | `src/job_hunt/resume_registry.py`, `select-resume-variant` CLI, `schemas/resume-selection.schema.json` |
| **Recent-job scan** (`--since 1h`) | `src/job_hunt/copilot.py`, `scan-recent-jobs` CLI, `schemas/recent-scan.schema.json` |
| **Orchestrator + decision log** | `plan_copilot_run` / `copilot-run` CLI → `data/runs/copilot-<ts>/decision-log.{json,md}` |
| Generation hook → registry | `generation._pick_curated_resume` consults the registry (wins only when its file exists; legacy lanes untouched otherwise) |
| Tests | `tests/test_resume_registry.py` (14), `tests/test_copilot.py` (13) |
| Docs | `docs/ai/architecture-copilot-level-1.5.md`, README + AGENTS.md sections |

The hardcoded title→resume mapping previously buried in `generation.py`
(`CURATED_RESUME_LANES`, two lanes, paths from another machine) is superseded by
the declarative registry, with the legacy lanes retained as a fallback.

## Tests

`python3 -m unittest discover -s tests -p 'test_*.py'` → **639 passed, 0 failed**
(612 baseline + 27 new). Also fixed one pre-existing date-bomb in
`tests/test_triage.py` (a "fresh" lead was hardcoded to `2026-05-17`, which had
silently aged past the 21-day ghost window) by anchoring it to wall-clock now.

## Dry-run (no submissions)

Ingested **5 real public Anthropic Greenhouse postings** via the tool's own
`ingest-url`, scored them, routed each to a resume variant, and ran the full
`copilot-run`. Routing on real titles:

| Posting | Routed variant | Confidence |
|---------|----------------|------------|
| Applied AI Engineer | `ai_engineer` | high |
| Full-Stack SWE, RL | `fullstack_product` | high |
| IT Systems / Client Platform Engineer | `platform_backend` | high |
| ML Infrastructure Engineer | `platform_backend` | high |
| Data Engineer | `generalist_swe` (fallback) | low |

All five flagged `needs_human_review` (the lane resume files aren't authored yet —
exactly the audit signal intended). All five scored fit-tier `no` because the
normalized profile is thin (only two sparse raw docs, no resume loaded, empty
`target_titles`) — the scoring works; the profile is the input gap. Example
artifacts: `examples/copilot/`. Nothing was submitted; the co-pilot cannot
submit.

---

## The four questions

### 1. Can I now say "apply to new jobs from the last hour"?

**Partially — up to a prepared, fully-logged packet, not a submitted one (by
design).** `scan-recent-jobs --since 1h` finds them, `score-lead` tiers them,
`select-resume-variant` picks the resume, `prepare-application` builds the
packet, `apply-posting` produces the browser handoff, and `copilot-run` chains
and logs all of it. The chain stops at the human submit gate. Two real
preconditions before it's *useful* end-to-end: (a) load a real resume + richer
profile so fit scores aren't uniformly low, and (b) author the per-lane resume
files under `profile/resumes/`.

### 2. What still requires human involvement?

- **The final Submit click — always** (hard invariant).
- **Account creation** approval (existing gate).
- **Authoring resume variants** under `profile/resumes/` (routing flags missing ones).
- **Reviewing flagged routes** — near-ties, low-confidence, unscored leads.
- **Reviewing generated answers/cover letters** before the browser fills them.
- **Calibration** — `calibrate-scoring` proposes; a human applies by hand.

### 3. What should Claude Cowork / Chrome handle vs. Claude Code?

- **Claude Code (this repo):** discovery, scoring, variant routing, packet prep,
  decision logging, handoff bundle — everything up to a *described*
  filled-but-unsubmitted form.
- **Claude in Chrome / Cowork:** drive the actual browser using the playbook +
  handoff bundle to *fill* the company form, then hand back to the human for the
  Submit click. The browser layer never auto-submits.

### 4. Next safest step toward real company-site form filling?

1. **Load a real profile + resume variants** so fit scoring and routing produce
   real strong-yes candidates (highest leverage, no new risk).
2. **One supervised live form-fill on a Greenhouse/Ashby posting** using the
   existing `apply-posting` handoff + Claude-in-Chrome, human watching, stopping
   before Submit — to validate the handoff contract on a real form.
3. **A field-mapping confidence report** (per field: value, source, confidence)
   surfaced for human review before the browser types anything — extends the
   existing answer-provenance machinery, keeps trust-first posture.
