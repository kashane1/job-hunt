---
title: Indeed auto-apply pre-Phase-1 spike findings
date: 2026-05-18
status: pending_live_run
problem_type: spike
component: docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md
tags:
  - spike
  - indeed-auto-apply
  - mcp
  - anti-bot
  - gmail
  - schema-validation
---

# Indeed auto-apply pre-Phase-1 spike findings

> **STATUS: PENDING LIVE RUN.** This file is the named deliverable of todo
> 046. It is a skeleton until a human executes
> [`docs/guides/indeed-auto-apply-spike-runbook.md`](../guides/indeed-auto-apply-spike-runbook.md)
> against a real browser + test Indeed account. Do **not** mark todo 046
> complete, and do **not** lock `schemas/application-plan.schema.json` in
> Phase 1b, until every section below contains an *observation* (not an
> assumption) and the acceptance checkboxes are ticked.
>
> The audit session that prepared this could not run the spike: it needs
> live Chrome, the Claude extension, a logged-in test Indeed account, and
> real postings — none of which exist in a repo/CI context. The harness
> (runbook + this skeleton + schema cross-references) is fully prepared so
> the live run is deterministic fill-in.

## Why this spike exists

Plan Enhancement-Summary items 24 (AI Recruiter), 25 (DOCX), 27 (Gmail
cursor) and the `application-plan.schema.json` `fields[]` shape are based on
2026 research inference, not empirical testing. If real Indeed diverges,
Phase 4/5 force schema churn and re-test. One day here de-risks 5–7 days
downstream.

---

## 1. MCP tool response shapes — `mcp__Claude_in_Chrome__*`

_PENDING LIVE RUN — fill from Runbook Probe 1._

| tool | observed request args | observed response keys | notes (truncation, async, blocking) |
|---|---|---|---|
| navigate | | | |
| read_page | | | |
| get_page_text | | | |
| find | | | |
| form_input | | | |
| file_upload | | | |
| click | | | |

## 2. Easy Apply field taxonomy (5 postings)

_PENDING LIVE RUN — fill from Runbook Probe 2._

Consolidated fields (maps to `application-plan.schema.json` `fields[]`):

| posting | field_id | question_text | answer_format | provenance |
|---|---|---|---|---|

**Schema gaps found:** _PENDING_ (yes/no — if yes, list every field whose
type is not in the `answer_format` enum; this is the primary go/no-go
signal for locking the schema).

Pagination / résumé handling observations: _PENDING_

## 3. AI Recruiter / Smart Screening detection

_PENDING LIVE RUN — fill from Runbook Probe 3._

- Triggered on: _PENDING_
- Stable selector signal (class / aria-label / data-*): _PENDING_ or
  **"NO STABLE SIGNAL FOUND"** (which would force heuristic/manual routing)

## 4. Gmail query DSL

_PENDING LIVE RUN — fill from Runbook Probe 4._

| operator | result |
|---|---|
| `newer_than:14d` | _PENDING_ |
| `after:YYYY/MM/DD` | _PENDING_ |
| `since:...` | _PENDING (expected: invalid)_ |
| `OR` case behavior | _PENDING_ |
| `historyId` present on messages | _PENDING_ |

## 5. Anti-bot behavior at planned pacing

_PENDING LIVE RUN — fill from Runbook Probe 5._

- Challenge observed: _PENDING_ (yes/no)
- If yes: after how many apps / minutes of session: _PENDING_
- Challenge page signature (status, text, `cf-ray`): _PENDING_
- Verdict on plan's log-normal(median 90s, tail 300s) + coffee-break
  cadence: _PENDING (validated / needs revision)_

---

## Acceptance criteria (todo 046)

- [ ] Catalogue of `mcp__Claude_in_Chrome__*` responses observed on Indeed
- [ ] Field taxonomy from 5 real Indeed Easy Apply postings
- [ ] AI Recruiter detection signal confirmed or refuted
- [ ] Gmail MCP query DSL behavior confirmed for proposed patterns
- [ ] Anti-bot behavior documented at ~90s pacing
- [ ] This file committed with observations replacing every _PENDING_

## Resources

- Runbook: [`docs/guides/indeed-auto-apply-spike-runbook.md`](../guides/indeed-auto-apply-spike-runbook.md)
- Plan: `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md`
- Schema under test: `schemas/application-plan.schema.json`
- todo: `todos/046-pending-p2-indeed-auto-apply-spike-before-phase1.md`
