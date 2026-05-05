# AGENTS.md

## Mission

Operate this repository as a trustworthy job-search system for one person. The goal is not maximum submission volume. The goal is high-quality discovery, honest application drafting, safe browser execution, and durable audit trails.

## Core Policies (hard invariants)

- Default to `strict` answer policy. Use supported facts from the candidate profile whenever possible. Inference is allowed only when clearly labeled. Do not fabricate unsupported facts unless runtime policy explicitly allows it.
- **The agent fills application forms but NEVER clicks the final Submit button.** Every per-surface playbook gates Step 6 on a human submit click. `apply_policy.auto_submit_tiers = []` is a compile-time invariant; runtime overrides cannot enable auto-submit. See `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`.
- V1 still requires explicit human approval before account creation.
- Never store passwords or secrets in git-tracked files. Redact secret-like fields from runtime attempt artifacts before writing reports. Store credentials in env vars or local ignored files such as `.env.local`.
- **Indeed.com and LinkedIn.com are allowlisted per `config/domain-allowlist.yaml`** (with playbooks `indeed-easy-apply.md`, `linkedin-easy-apply.md`). Other sites in `HARD_FAIL_URL_PATTERNS` continue to hard-fail unless explicitly allowlisted.
- **Glassdoor automation is a board-specific exception** via `glassdoor-easy-apply.md`, not a global allowlist; `glassdoor.com` is not allowlisted for ingestion/discovery.

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
