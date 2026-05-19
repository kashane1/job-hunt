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

## Run scope (2026-05-18 live session)

Constrained run: the connected Chrome is logged into the operator's
**real** job-search Indeed account (not a disposable test account).
Per the runbook's safety rule and the project trust posture, this session
runs **read-only Probes 1–3 only**:

- **Probe 5 (anti-bot): NOT RUN** — deliberately skipped by operator
  direction; would risk the real account. Remains `_PENDING (test account
  required)_`.
- **Probe 1:** only non-mutating tools (`navigate`, `get_page_text`,
  `read_page`, `find`) exercised. Mutating tools (`form_input`,
  `file_upload`, click-to-advance) are **deferred to a test account** —
  their shapes are NOT fabricated.
- **Probe 2:** field taxonomy captured by *reading* the Easy Apply form
  structure without filling or submitting; only `question_text` +
  `answer_format` recorded — no personal field values.
- No screenshots written under `data/` this session (real-account PII).

## 1. MCP tool response shapes — `mcp__Claude_in_Chrome__*`

_Read-only subset — Probe 1 (mutating tools deferred, see Run scope)._

Observed 2026-05-18 on a live Indeed `viewjob` page (read-only subset).

| tool | observed request args | observed response shape | notes (truncation, async, blocking) |
|---|---|---|---|
| navigate | `{url, tabId}` | plain-text `"Navigated to <url>"` + `Tab Context:` block (executed tabId, available tabs list) | **Does NOT block on load.** Immediately after `navigate`, the returned Tab Context still showed the *pre-nav* title/url (`New Tab`/`chrome://newtab/`). Phase 5 MUST poll (re-`read_page`/`find`) after navigate; never assume DOM ready. |
| get_page_text | `{tabId}` | `Title:` / `URL:` / `Source element:` / `---` / flattened body text / `Tab Context:` | Text is **flat/linearized** (no DOM segmentation). HTML entities NOT fully cleaned (`&nbsp;` appears literally → downstream regex must strip). No char cap hit on a full JD; cap value still unknown. The page's **employer custom questions were present in this text** (see §2). |
| read_page | `{tabId, filter:"interactive", max_chars}` | indented list of `role "name" [ref_N] attrs` + `Viewport: WxH` + `Tab Context:` | `ref_N` is the **element handle** other tools consume. `interactive` filter keeps it compact. No pixel coords. Easy Apply control surfaced as `button "Apply with Indeed" [ref_16]`. |
| find | `{query (natural language), tabId}` | `"Found N matching element"` + `- ref_N: role "name" (role) - <rationale>` + `Tab Context:` | Returns **`ref` handles only — NO bounding-box pixel coordinates** (contradicts a planning assumption). Ref is consistent with `read_page` (`ref_16` both). Read-only. |
| form_input | _deferred_ | _NOT OBSERVED_ | Mutating — would alter a real-account application. Deferred to test-account run; **not fabricated**. |
| file_upload | _deferred_ | _NOT OBSERVED_ | Mutating (file chooser). Deferred to test-account run. |
| click | _deferred_ | _NOT OBSERVED_ | Mutating / initiates apply flow on a real account. Deferred to test-account run. |

### Cross-cutting finding — `find` returns refs, not pixel boxes (affects todo 045 + Phase 5)

Runbook Probe 1 and the screenshot-PII plan (todo 045 / Phase 5) assumed
the agent derives PII blur **bounding boxes** from `find`/`read_page`.
**Neither returns pixel coordinates** — only `ref_N` element handles and a
viewport size. The `sanitize-screenshot --regions '[[l,t,r,b],…]]'`
contract therefore has **no observed coordinate source** in the Claude-in-
Chrome surface probed here. Phase 5 must either (a) find a coordinate-
returning tool (e.g. an `inspect`/geometry call — not yet probed), or
(b) change the PII strategy from region-blur to full-frame redaction /
not screenshotting the filled form at all. **Flagged loudly: this is a
real plan-assumption break, independent of the test-account work.**

## 2. Easy Apply field taxonomy

**Partial — 1/5 postings, custom-question subset only** (read-only on a
real account; the standard contact/résumé modal fields were NOT opened —
that requires the test-account run). Useful structural finding even so:
the **employer-custom screening questions are exposed on the `viewjob`
page itself**, before entering the apply modal — so taxonomy capture does
not strictly require driving the flow for the bespoke-question portion.

| posting | field_id | question_text (employer-custom) | answer_format | provenance |
|---|---|---|---|---|
| P1 (jk dfa37…) | q_0to1_shipped | "Have you built and shipped a 0→1 product, tool, or workflow yourself? Briefly describe it and the technologies used." | `text` | inferred |
| P1 | q_langchain_agents | "Have you worked with LangChain, AI agents, or multi-agent AI workflows? If yes, briefly describe what you built." | `text` | inferred |
| P1 | q_independent_exp | "Describe your experience in consulting, PM, AM, sales, or engineering roles where you managed work independently without constant direction." | `text` | inferred |
| P1 | doc_resume | (Application Process) résumé | `file` | profile |
| P1 | doc_cover_letter | (Application Process) 1–2 paragraph cover letter | `text` | curated_template |
| P1 | link_portfolio | (Application Process) portfolio/GitHub link (optional) | `text` (no `url` enum member) | profile |

**Schema gaps found:** **none in the observed subset** — all map to
existing `answer_format` enum members (`text`/`file`). One soft note: a
portfolio *URL* has no dedicated enum member and falls back to `text`;
acceptable, not a gap. **NOT sufficient to lock `application-plan.schema.json`:**
only 1/5 postings and zero modal/standard fields (name/email/phone/résumé-
picker/`yes_no` knockouts/`multi_select`) were observed — those are exactly
where churn risk lives. Schema-lock go/no-go stays **blocked on the
test-account run**.

Pagination / résumé handling: NOT observed (modal not entered on real
account). _PENDING test-account run._

## 3. AI Recruiter / Smart Screening detection

**NOT observable read-only on a real account.** The adaptive
chat/screening widget renders *inside* the apply flow; reaching it on the
real account would require clicking "Apply with Indeed" and initiating an
application (state-mutating on the real identity) — out of scope for this
session. The `viewjob` page DOM (`read_page interactive`) showed no
screening-widget container.

- Triggered on: _PENDING (test-account run — enter Easy Apply flow)_
- Stable selector signal: _PENDING_ — capture container class /
  `aria-label` / `role` / stable `data-*` once in-flow on a test account;
  if absent, record **"NO STABLE SIGNAL FOUND"** (forces heuristic/manual
  routing in the plan).

## 4. Gmail query DSL — OBSERVED 2026-05-18 (live, shape-only)

Connector: `mcp__…__search_threads` / `get_thread`. Probed with
`pageSize:1` and content-free tokens; only response *structure* recorded
(no subjects/senders/snippets retained).

| operator | result |
|---|---|
| `newer_than:14d` | **Accepted** — returned a thread; date window honored |
| `after:YYYY/MM/DD` | **Accepted** — parsed; 0-match query returned cleanly |
| `since:...` | **NOT a date operator — fails *silently*.** No error; treated as a free-text term (no date filtering applied). More dangerous than "rejected": code using `since:` would not raise, it would just silently not filter. Plan's "don't use `since:`" assertion confirmed; failure mode is silent, not loud. |
| `OR` / `{}` grouping | Supported per connector schema (authoritative); not re-probed to limit inbox exposure |
| `historyId` present on messages | **NO.** Absent from both thread and message objects (see cross-cutting finding) |

### Cross-cutting finding — Gmail connector is thread-API, not `gmail_search_messages`

This **changes Phase 8** and should be recorded even before the browser run:

- **Granularity:** the connected MCP is **thread-oriented** (`search_threads` → threads each containing `messages[]` with `snippet`/`subject`/`sender`/`labelIds`/`toRecipients`/`date`/`id`; `get_thread` for full bodies). The plan assumed message-level `gmail_search_messages`. Phase 8 must map onto threads.
- **No `historyId` anywhere** in the response → the plan's assumed `historyId`-based incremental cursor is **not implementable on this connector**. Phase 8 must cursor by `nextPageToken` pagination + a `newer_than:`/`after:` date window (or a label-delta), not Gmail History API.
- **Zero-result shape is `{}`** (no `threads` key, no `nextPageToken`) — *not* `{"threads":[]}`. Phase 8 parsing must treat an absent `threads` key as empty, or it will `KeyError` on every empty poll.
- **Pagination:** token-based via top-level `nextPageToken` (string); `pageSize` max 50, default 20.

## 5. Anti-bot behavior at planned pacing

**NOT RUN — and not runnable on the real account.** Probe 5 deliberately
triggers anti-bot by filling applications; on the operator's real
job-search identity that risks an account challenge/lock. Skipped by
explicit operator direction. Remains the single biggest unvalidated plan
assumption (Phase 7 `apply-batch` pacing model).

- Challenge observed: _PENDING (test account required)_
- Verdict on plan's log-normal(median 90s, tail 300s) + coffee-break
  cadence: _PENDING — must be probed on a disposable test account before
  Phase 7 builds `apply-batch`._

---

## Acceptance criteria (todo 046)

- [~] Catalogue of `mcp__Claude_in_Chrome__*` responses — **partial**:
  read-only tools (`navigate`/`get_page_text`/`read_page`/`find`) observed
  2026-05-18; mutating tools (`form_input`/`file_upload`/`click`) deferred
  to test account (not fabricated).
- [~] Field taxonomy — **partial**: 1/5 postings, employer-custom question
  subset (observable on `viewjob`); standard modal fields pending test acct.
- [ ] AI Recruiter detection — pending (in-flow; needs test account).
- [x] Gmail MCP query DSL behavior confirmed (2026-05-18; + thread-API/
  no-historyId cross-cutting finding).
- [ ] Anti-bot behavior at ~90s pacing — pending (test account required).
- [~] File committed with observations — **this session's observations
  committed; `_PENDING` remain for test-account-only probes.**

### Session log

- **2026-05-18 (constrained live run):** Probe 4 fully done. Probes 1–2
  partial (read-only, real account). Probes 3 & 5 deferred (require
  entering the apply flow / test account). **Status stays
  `pending_live_run`; todo 046 stays open.** Two plan-assumption breaks
  surfaced without needing the test account: (1) Gmail connector is a
  thread API with no `historyId` (Phase 8 re-design); (2) `find`/`read_page`
  return refs not pixel boxes (todo 045 / Phase 5 screenshot-region
  strategy break). Net: the spike has already paid for itself on
  de-risking, before the (still-required) test-account run.

## Resources

- Runbook: [`docs/guides/indeed-auto-apply-spike-runbook.md`](../guides/indeed-auto-apply-spike-runbook.md)
- Plan: `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md`
- Schema under test: `schemas/application-plan.schema.json`
- todo: `todos/046-pending-p2-indeed-auto-apply-spike-before-phase1.md`
