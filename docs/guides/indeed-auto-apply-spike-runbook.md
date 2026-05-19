# Indeed Auto-Apply Pre-Phase-1 Spike — Runbook

This is the **execution runbook** for todo 046. The spike de-risks the
Indeed auto-apply plan (`docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md`)
by replacing inferred assumptions with empirically observed facts before
Phase 1b locks `schemas/application-plan.schema.json`.

> **This spike cannot be run in CI or from the repo alone.** It requires a
> human at a real browser: Chrome with the Claude extension, logged into a
> **test** Indeed account, against live postings. No code changes are made
> by the spike. Record observations into
> [`docs/solutions/indeed-auto-apply-spike-findings.md`](../solutions/indeed-auto-apply-spike-findings.md)
> as you go; that file ships with the acceptance checkboxes.

## Why a test account

Indeed's 2026 Job Seeker Guidelines prohibit automated applying and do not
distinguish personal automation from scraping (see
[`indeed-auto-apply.md`](indeed-auto-apply.md) risk section). Probing
anti-bot behavior risks the account used. Use a disposable test account,
not your real job-search identity.

## Pre-flight

- [ ] Chrome + Claude-in-Chrome extension installed and connected
- [ ] Logged into a **test** Indeed account
- [ ] 5 distinct **Indeed Easy Apply** postings bookmarked (mix of
      seniority/company size; confirm "Easy apply" badge, not redirect)
- [ ] Gmail MCP connected to the inbox the test account uses
- [ ] A scratch file open for raw notes; the findings doc open alongside

---

## Probe 1 — MCP tool response shapes (todo 046 AC#1)

For **one** real Indeed posting, invoke each tool once and paste the raw
response shape (keys, types, truncation behavior) into the findings doc.

Tools to log (each `mcp__Claude_in_Chrome__*`):

1. `navigate` → to the posting URL. Record: returned status/shape, whether
   it blocks on load, any consent/interstitial.
2. `read_page` / `get_page_text` → record the structure: is text flat, or
   segmented? Are form fields present in the text dump? Character cap?
3. `find` → search for the "Apply now" / "Continue" control. Record: does
   it return bounding boxes? selector handles? multiple matches?
4. `form_input` → fill ONE text field. Record: arguments it expects
   (selector? ref? value?), success/failure shape.
5. `file_upload` → attach a dummy résumé PDF. Record: argument shape, how
   it reports the chooser, success signal.
6. `click` → advance one step. Record: navigation/async behavior, how a
   step transition is observable.

Deliverable: a per-tool "observed request args / observed response keys"
table in the findings doc. This is what Phase 1b uses to stop guessing the
MCP surface.

---

## Probe 2 — Easy Apply field taxonomy on 5 postings (todo 046 AC#2)

For each of the 5 postings, walk the Easy Apply flow **without submitting**
(the human-submit invariant holds even in the spike) and record every field
in this shape — it maps 1:1 to `application-plan.schema.json` `fields[]`:

| posting | field_id (your label) | question_text (verbatim) | answer_format | likely provenance |
|---|---|---|---|---|

`answer_format` MUST be one of the schema enum:
`yes_no | text | multi_select | number | file | date | unknown`
(confirm the full enum in `schemas/application-plan.schema.json`).
`provenance` candidates: `profile | curated | curated_template | inferred | none`.

Capture:
- screenshots of each distinct step (sanitize before saving anywhere under
  `data/` — see `sanitize-screenshot`),
- any field whose type does **not** fit the current enum (this is the
  schema-churn signal the spike exists to catch — flag loudly),
- multi-step pagination shape (how many screens, what advances them),
- résumé handling: upload vs. "use Indeed résumé" vs. parsed-profile.

Deliverable: the consolidated field taxonomy table + an explicit
"schema gaps found: yes/no — details" line.

---

## Probe 3 — AI Recruiter / Smart Screening detection (todo 046 AC#3)

Plan item 24 assumes Indeed's adaptive chat/video/voice screening can be
detected by class name / aria-label and routed to an `unknown_question`
tier-2 pause. Validate or refute:

- [ ] On any posting that triggers an adaptive chat/screening widget,
      capture the DOM: container class names, `aria-label`, `role`, any
      stable `data-*` attribute.
- [ ] If none of the 5 trigger it, search Indeed for roles that do
      (high-volume retail/support roles tend to) and capture one.
- [ ] Record the **exact, stable** selector signal, or write
      "NO STABLE SIGNAL FOUND" — that outcome changes the plan
      (detection moves from selector to heuristic/manual).

Deliverable: confirmed selector pattern, or an explicit refutation.

---

## Probe 4 — Gmail query DSL (todo 046 AC#4)

Phase 8 relies on `newer_than:` / `after:` and asserts `since:` is invalid.
Confirm against the real Gmail MCP:

- [ ] `newer_than:14d` → accepted, returns results
- [ ] `after:YYYY/MM/DD` → accepted
- [ ] `since:...` → rejected / no-op (confirm it is NOT a valid operator)
- [ ] `OR` uppercase vs lowercase behavior
- [ ] `historyId` availability on returned messages (incremental cursor
      in Phase 8 depends on it)

Deliverable: a confirmed/`rejected` line per operator.

---

## Probe 5 — Anti-bot behavior at planned pacing (todo 046 AC#5)

The plan specifies log-normal pacing (median ~90s, tail to 300s) + coffee
breaks every 4-6 applications + a ~20/day cap. Probe conservatively on the
test account:

- [ ] Fill (do **not** submit) 3 applications spaced ~90s apart.
- [ ] After each, record: any Cloudflare/Akamai interstitial, "unusual
      activity" challenge, CAPTCHA, or rate-limit page (status + visible
      text + `cf-ray` header if present).
- [ ] Note **when** a challenge first appears (which attempt, elapsed
      session time) — this calibrates the coffee-break cadence.
- [ ] Stop immediately if the account is challenged/locked; record where.

Deliverable: "challenge observed: yes/no; if yes, after N apps / M minutes;
page signature: …". This either validates the pacing model or forces a
revision before Phase 7's `apply-batch` is built.

---

## Exit criteria

The spike is done when every checkbox in
[`docs/solutions/indeed-auto-apply-spike-findings.md`](../solutions/indeed-auto-apply-spike-findings.md)
is filled with an **observation** (not an assumption), and the
"schema gaps found" line in Probe 2 is answered. Then todo 046 can be
marked complete and Phase 1b can lock the schema with confidence.
