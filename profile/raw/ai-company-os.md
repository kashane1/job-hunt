---
document_type: project_note
title: ai-company-os Project Note
tags:
  - ai-systems
  - agent-orchestration
  - automation
  - python
  - pydantic
  - postgres
  - redis
  - github
  - worktrees
  - approvals
  - auditability
  - ios
  - shipped-product
---

# ai-company-os

## One-line

An AI-first engineering system I designed and run: I direct a fleet of AI
coding agents to discover product niches, build apps, and ship them — with
typed tool boundaries, human approval gates, and a full audit trail so the
system can run unattended without me losing control of what it did or why.

## Timeline and scale (use these exact, verifiable facts — do not inflate)

- Public repo: github.com/kashane1/ai-company-os.
- First commit 2026-03-27. Built intensively over roughly two months.
- ~565 commits, conventional-commit history, CI on every change.
- Three iOS products produced *by the system* live in the repo: life-clock,
  catchbook, after-plans.
- The high commit count and large parallel branch set are not noise — they
  are the output of the AI-first pipeline working as designed. The velocity
  is the thesis, not an accident.
- Never claim a tenure ("over the last year") or an unbounded production soak
  ("running for months"). The true, stronger claim: a two-month intensive
  build that already ships real products and runs my recurring workflows
  behind approval gates with an audit trail.

## What it actually is

A local-first, policy-driven control plane (Python) where the *platform* owns
orchestration and AI agents only execute within boundaries it defines:

- The platform is the brain. Codex is the engineer. Postgres is memory.
  Redis is the queue. GitHub is the delivery lane.
- Lane-specific workers: engineering, iOS, go-to-market, App Store release,
  skill-evolution, plus a runtime supervisor and an approval reviewer
  (`apps/worker-*`, `apps/runtime-supervisor`, `apps/approval-reviewer`).
- Niche discovery → build → market → release is a real pipeline, not a
  description: `worker-gtm`, `worker-ios`, `worker-appstore`, `products/`.

## The engineering that makes it trustworthy (each backed by code)

- **Typed tool surface.** Tools and task contracts are typed via frozen
  dataclass schemas with enum-constrained fields and explicit
  `to_dict`/`from_dict` boundaries in `packages/schemas` (goal, approval,
  event, postmortem, release, product); Pydantic models guard the API
  surface. Malformed arguments and unknown actions are rejected at the
  boundary instead of failing deep inside a run.
- **Human-in-the-loop approval gates.** Any consequential or irreversible
  action pauses for explicit approval — `packages/policies/approvals.py`,
  `packages/tools/primitives/approvals.py`, `apps/approval-reviewer`,
  `apps/api` approval endpoint.
- **Audit / postmortem store.** Every run writes a structured artifact with
  retention policy — `packages/db/postmortem_store.py`,
  `packages/policies/postmortem_retention.py`. After the fact I can answer
  exactly what an agent did and why, not just what it claimed.
- **Repo safety.** Mutations happen through isolated git worktrees, not
  hidden prompt logic; runtime state lives in `state/`, never in source.
- **Adversarial security tests.** Redaction tests assert secrets
  (sk-…, AKIA…) never leak into artifacts; `.env` gitignored; clean git
  history with no real secrets ever committed.
- **Process maturity rare in a solo repo.** CI on every change, a
  tests-must-ship-with-code policy, PR template, conventional commits,
  95 Python test files.

## Why it matters / what it demonstrates

- Systems design and ownership boundaries, not prompt-chaining.
- Safety engineering: typed boundaries, approval gates, auditable rollback —
  the same properties that make any production system trustworthy.
- AI orchestration at real scale: directing a parallel agent fleet to ship
  software, with the observability to stay in control of it.
- End-to-end product delivery: the system has shipped real iOS apps, so this
  is evidence of outcomes, not a framework with no users.
- Honest framing is the point: everything above is checkable in 30 seconds
  via `git log` and the named files. The story is built to survive scrutiny.
