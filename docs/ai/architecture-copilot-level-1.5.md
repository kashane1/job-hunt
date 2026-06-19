# Level 1.5 Application Co-Pilot — Architecture Note

**Status:** implemented (2026-06-18)
**Scope:** turn the existing job-hunt repo into a safe co-pilot that can take a
북-star instruction —

> "Find any new software-engineering jobs posted in the last hour that strongly
> match my resume, choose the best resume variant, generate tailored materials,
> and prepare the application on the company site up to — but not including —
> final submit."

— and execute it as a chain of **concrete, auditable CLI steps** that stop at a
human-approved submit gate. (Call it the north-star instruction.)

This note explains what already existed, the minimum we added, and the exact
command chain.

---

## Design principles (unchanged from `AGENTS.md`)

1. **Human owns the submit click.** `apply_policy.auto_submit_tiers = []` is a
   compile-time invariant. The co-pilot prepares; it never submits.
2. **No "AI magic."** Every decision is a deterministic, inspectable artifact:
   you can read *why* a job matched, *which* resume variant was chosen and on
   what evidence, and *what* a human still has to do.
3. **Propose, don't mutate selection criteria.** Routing reads config; it never
   rewrites `scoring.yaml` or the registry.
4. **Trust over volume.** A small number of well-prepared, fully-logged packets
   beats a flood of auto-filled forms.

---

## The six-step workflow → existing vs. new

| # | Requested step              | Backing command(s)                              | Status |
|---|-----------------------------|-------------------------------------------------|--------|
| 1 | `scan-recent-jobs --since`  | `scan-recent-jobs` (new) over `discover-jobs`   | **new wrapper** |
| 2 | `score-job-fit`             | `score-lead` / discovery auto-score (existing)  | existing |
| 3 | `select-resume-variant`     | `select-resume-variant` (new) + registry        | **new** |
| 4 | `generate-application-packet` | `prepare-application` + `generate-*` (existing) | existing |
| 5 | `prepare-browser-application` | `apply-posting` handoff + playbooks (existing)  | existing |
| 6 | `human-review-submit`       | hard invariant; `copilot-run` stops here        | existing |

Steps 2, 4, 5, 6 were already built and tested. The genuine gaps were a
**time-windowed scan**, a **config-driven resume-variant registry with a logged
routing decision**, and an **orchestrator that chains the steps and writes one
decision log per run**. Those are what we added.

---

## What we added (Level 1.5 delta)

### 1. Resume variant registry — `config/resume-variants.yaml`

Previously the title→resume mapping was hardcoded in `generation.py`
(`CURATED_RESUME_LANES`, two lanes, absolute paths from another machine). It is
now a declarative, versioned registry:

```yaml
schema_version: 1
default_variant: generalist_swe
variants:
  - id: ai_engineer
    label: AI / ML Engineer
    title_patterns: [ai engineer, ml engineer, machine learning, llm, genai]
    emphasis_skills: [python, pytorch, llm, rag, ml, nlp]
    seniority_bands: [mid, senior]
    resume_path: profile/resumes/ai-engineer.md
  - id: generalist_swe        # default lane (empty title_patterns = wildcard)
    title_patterns: []
    resume_path: examples/profile/raw/resume.md
```

Loaded and validated by `src/job_hunt/resume_registry.py`. Schema:
`schemas/resume-variant-registry.schema.json`. `generation.py` now consults the
registry first and falls back to the legacy hardcoded lanes, so existing
behavior is preserved when the config is absent.

### 2. Job-title → resume routing — `select-resume-variant`

`src/job_hunt/resume_registry.py:route_lead()` scores every variant against a
lead with a transparent rubric (no model call):

- **title match** — fraction of a variant's `title_patterns` present in the
  lead title (dominant signal),
- **skills overlap** — overlap of `emphasis_skills` with the lead's matched
  skills / keywords,
- **seniority band** — match against the lead's inferred seniority.

It picks the highest, breaking ties by registry order. The **default variant**
is used when no specialized variant clears a minimum confidence. The command
writes a `resume-selection.json` decision artifact (schema:
`schemas/resume-selection.schema.json`) capturing the winner, score,
confidence, matched evidence, ranked alternatives, fallback flag, and an
explicit `needs_human_review` with reasons (missing resume file on disk, a
near-tie between lanes, low confidence, or a lead that was never scored).

### 3. Recent-job scan — `scan-recent-jobs --since 1h`

`src/job_hunt/copilot.py:filter_recent_leads()` filters existing leads to a
wall-clock window (`30m`, `1h`, `2d`, `1w`, or an ISO timestamp). The effective
timestamp is the newest of `observed_sources[].discovered_at`,
`listing_updated_at`, and `ingested_at`. It groups results by fit tier
(`strong_yes` / `maybe` / `no`, from the existing `scoring.yaml` thresholds) and
writes a `recent-scan.json` artifact. With `--discover` it first runs the real
`discover-jobs` poll; by default it scans the leads already on disk so it is
runnable and testable offline.

### 4. Orchestrator — `copilot-run --since 1h`

`src/job_hunt/copilot.py:plan_copilot_run()` chains scan → route-variant →
packet-plan for every lead at or above the configured tier, and writes **one
decision log per run** under `data/runs/copilot-<ts>/` (both `decision-log.json`
and a human-readable `decision-log.md`). For each job it records: why it
matched (fit score + rationale), which variant was selected and why, the
exact follow-up commands to generate the packet and prepare the browser, and
what still needs human review. It is **plan/dry-run by default** and never
generates final content or submits — it stops at the human gate by construction.

---

## End-to-end command chain

```bash
# 1. Scan the last hour of discovered leads, grouped by fit tier
python3 scripts/job_hunt.py scan-recent-jobs --since 1h

# 2. (scoring already happened during discovery; re-score on demand)
python3 scripts/job_hunt.py score-lead --lead data/leads/<id>.json

# 3. Route the lead to its best resume variant (logged decision)
python3 scripts/job_hunt.py select-resume-variant --lead data/leads/<id>.json

# 4. Build the application packet (plan + tailored resume/cover/answers)
python3 scripts/job_hunt.py prepare-application --lead data/leads/<id>.json

# 5. Emit the browser handoff bundle (agent fills the form, never submits)
python3 scripts/job_hunt.py apply-posting --draft data/applications/<draft>/plan.json

# 6. Human reviews the prepared form and clicks Submit.

# --- or run the whole chain as one planned, fully-logged dry-run: ---
python3 scripts/job_hunt.py copilot-run --since 1h --min-tier maybe
```

## Artifact map

| Artifact | Path | Schema |
|----------|------|--------|
| Resume variant registry | `config/resume-variants.yaml` | `resume-variant-registry` |
| Recent-scan result | `data/runs/recent-scan-<ts>.json` | `recent-scan` |
| Resume routing decision | `data/applications/<lead>-resume-selection.json` | `resume-selection` |
| Co-pilot run decision log | `data/runs/copilot-<ts>/decision-log.{json,md}` | `recent-scan` (embeds) |

## Boundary: Claude Code vs. Claude Cowork / Chrome

- **Claude Code (this repo):** discovery, scoring, variant routing, packet
  preparation, decision logging, and the handoff bundle — everything up to a
  filled-but-unsubmitted form description.
- **Claude in Chrome / Cowork:** drives the actual browser using the playbook +
  handoff bundle to *fill* the company form, then **hands control back to the
  human for the final Submit click**. The browser layer never auto-submits.
