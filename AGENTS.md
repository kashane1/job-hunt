# AGENTS.md

## Mission

Operate this repository as a trustworthy job-search system for one person. The goal is not maximum submission volume. The goal is high-quality discovery, honest application drafting, safe browser execution, and durable audit trails.

## Core Policies (hard invariants)

- Default to `strict` answer policy. Use supported facts from the candidate profile whenever possible. Inference is allowed only when clearly labeled. Do not fabricate unsupported facts unless runtime policy explicitly allows it.
- **The agent fills application forms but NEVER clicks the final Submit button.** Every per-surface playbook gates Step 6 on a human submit click. `apply_policy.auto_submit_tiers = []` is a compile-time invariant; runtime overrides cannot enable auto-submit. See `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`.
- V1 still requires explicit human approval before account creation.
- **Scoring calibration is propose-only.** `calibrate-scoring` turns outcome analytics into proposed `config/scoring.yaml` / profile-evidence deltas and writes a reviewable artifact under `data/calibration/`. It never edits `scoring.yaml`, the profile, or the answer bank — a human applies changes by hand. There is no `--apply` path by design (an automated loop mutating its own selection criteria from a small biased sample is the failure mode the trust posture prevents).
- **Inbound-email triage is verification-bound and propose-not-auto for non-allowlisted outcomes.** `triage-inbox` advances a lead's Model-B status (`{lead_id}-status.json`, what `calibrate-scoring` reads) only when the sender is allowlisted **or** the DKIM `header.d=` registrable domain equals the *stored* company domain (never the `From`, display name, or body). A rejection/offer from a non-allowlisted (even DKIM-matched) sender is quarantined to `data/applications/_suspicious/` for human promotion, never silently applied (anti-spoof). The quarantine is resolved through the `triage-review-list` / `triage-review-promote <message_id> [--lead --stage] [--confirm]` / `triage-review-dismiss <message_id> --reason` triad: promote is **propose-by-default and only writes under explicit `--confirm`**; resolving (promote or dismiss) deletes the quarantine file and appends to a `.audit.jsonl` trail so `check-integrity` stops counting it. Triage is idempotent (shared `event_id`), never moves a stage backward, redacts email subject/body before any on-disk write, and feeds the loop only — it never tunes scoring. `check-integrity.unbridged_confirmations` detects A↔B divergence; `triage-inbox` replays it.
- Never store passwords or secrets in git-tracked files. Redact secret-like fields from runtime attempt artifacts before writing reports. Store credentials in env vars or local ignored files such as `.env.local`.
- **Indeed.com and LinkedIn.com are allowlisted per `config/domain-allowlist.yaml`** (with playbooks `indeed-easy-apply.md`, `linkedin-easy-apply.md`). Other sites in `HARD_FAIL_URL_PATTERNS` continue to hard-fail unless explicitly allowlisted.
- **Glassdoor automation is a board-specific exception** via `glassdoor-easy-apply.md`, not a global allowlist; `glassdoor.com` is not allowlisted for ingestion/discovery.

## Level 1.5 Co-Pilot

- **Resume variant routing is config-driven and logged.** `config/resume-variants.json`
  (schema `resume-variant-registry`) maps job-title lanes to pre-authored resume
  files. `select-resume-variant --lead <lead>` routes a lead via
  `resume_registry.route_lead` and writes a `<lead_id>-resume-selection.json`
  decision artifact (schema `resume-selection`): chosen variant, score,
  confidence, matched evidence, alternatives, and an explicit
  `needs_human_review` with reasons. Routing reads the registry; it never
  rewrites it.
- **`scan-recent-jobs --since <window>`** filters discovered leads to a
  wall-clock window (`30m`/`1h`/`2d`/`1w`/ISO) and groups by fit tier.
- **`copilot-run`** chains scan → variant routing → per-job packet plan and
  writes one decision log per run under `data/runs/copilot-<ts>/`. It is
  plan/dry-run only: it generates no final content and **never submits** — the
  human submit gate is preserved by construction.

## Browser Guardrails

Soft tab limit 10, hard tab limit 15. Reuse the current tab whenever possible. Close background tabs aggressively before opening new ones. If the hard limit is reached, stop safely and record the failure.

## Artifact Layout

- `profile/normalized/` — machine-readable profile context
- `data/leads/` — normalized leads and scoring output
- `data/applications/` — application drafts and JSON reports
- `data/runs/` — run summaries
- `docs/reports/` — human-readable markdown reports

## Reporting Requirements

Every application attempt records: approval-required + obtained, account-creation approval-required + obtained, answers used and provenance, confidence, blockers, browser tab metrics, submission confirmation, secret-redaction status.

## Document Conventions

Profile documents use YAML frontmatter (`document_type`, `title`, `tags`).

**Schema versioning.** Long-lived state files (cursors, caches) carry `schema_version` as an integer `const` starting at `1` (e.g. `schemas/discovery-cursor.schema.json`). Per-run / rebuildable derived artifacts do not require versioning. Migrate via a one-shot script, or delete-and-rescan when the artifact is a rebuildable derived file. (Profile/content documents predate this and use a string `schema_version` — not retrofitted.)

## Safety Overrides

If runtime configuration conflicts with these defaults, prefer the stricter option unless the user explicitly asked for looser behavior in the current session.

## Implementation references (load only when working in that area)

- CLI output contract, error-code enums, `ats_check` and `apps-dashboard` state machines, URL ingestion / PDF export safety: [docs/ai/batches/batch-2-ingestion.md](docs/ai/batches/batch-2-ingestion.md)
- Active job discovery (rate limiting, robots, anti-bot, review entries, schema versioning, `DiscoveryError`): [docs/ai/batches/batch-3-discovery.md](docs/ai/batches/batch-3-discovery.md)
- Autonomous Indeed application (v4 human-submit policy, allowlist, playbooks, lifecycle, anti-bot pacing, confirmation verification, `ApplicationError`/`PlanError`): [docs/ai/batches/batch-4-apply.md](docs/ai/batches/batch-4-apply.md)
- Per-surface playbooks: `playbooks/application/{surface}.md`

## Files agents should NOT read by default

- `docs/plans/*.md` — historical implementation plans, often >1k lines. Load only when explicitly working on that plan.
- `docs/reports/` — generated reports; PII-adjacent.
- `data/**` — runtime artifacts; PII-adjacent. Read only when explicitly debugging that data.
- `.claude/worktrees/**` — recursive copies of this repo.
