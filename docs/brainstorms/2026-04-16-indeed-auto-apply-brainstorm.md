---
date: 2026-04-16
topic: indeed-auto-apply
---

# Indeed Auto-Apply Brainstorm

## What We're Building

The job-hunt repo currently stops at "find + rank + tailor resume" for Greenhouse/Lever/careers pages, and hard-fails any Indeed URL at the ingestion and discovery layers as a login-walled site. This feature reverses that policy for Indeed specifically and extends the pipeline through the **apply** step so that the target workflow works end-to-end:

> *"Apply to the top 10 Indeed postings that match my profile."*

The system discovers Indeed postings, ranks them with the existing six-factor scorer, tailors a resume per posting using the existing variant generator, and then **drives the user's Chrome browser via the Claude-in-Chrome MCP** to fill and submit applications. A tiered confidence policy decides which applications auto-submit and which pause for human review. Every attempt writes a structured audit record.

The target apply surfaces are (a) Indeed Easy Apply (form on indeed.com) and (b) external ATS redirects from Indeed listings (Greenhouse, Lever, Workday, Ashby, etc.). Company-direct redirects fall back to "generate draft, human finishes."

## Why This Approach

Three architectural shapes were considered. The chosen shape is **Hybrid: agent-driver + structured artifacts**.

### Chosen: Hybrid Agent-Driver

Claude Code is the browser driver. It calls `mcp__Claude_in_Chrome__*` tools directly, guided by a structured playbook. Python CLI provides orchestration and state: `prepare-application` assembles a draft + form plan, the agent drives the browser and writes checkpoint artifacts, `record-attempt` closes the loop.

**Pros**
- Minimal new Python code; tests where they matter (plan generation, answer resolution, state transitions)
- Agent absorbs form redesigns — no brittle selector maps to maintain per ATS
- Batch-10 runs as a Python orchestrator that fans out to sub-agent sessions with durable state
- Matches the repo's existing style: strong CLI + artifact audit trail + playbook-driven agent behavior

**Cons**
- Playbook must be disciplined about checkpoint artifacts
- Less deterministic than pure code — failures look like "agent misread a field" rather than "selector returned null"

### Rejected: Thin Executor

Smallest code footprint, but batch-10 becomes 10 long freeform agent sessions with weak audit trails and policy living entirely in prose. Loses the repo's auditability invariant.

### Rejected: Declarative Mappers

Per-ATS Python modules with CSS selectors and question pattern maps. Deterministic but reinvents what an agent perceives for free, and every Indeed/Workday redesign becomes a maintenance emergency.

## Key Decisions

### Approval posture: Tiered by confidence
Auto-submit only when all three hold: (a) every form answer is a **supported fact** from the profile or from the curated answer bank (`source: curated, reviewed: true`), (b) the generated resume passes `ats-check` with `passed` (not `warnings`), (c) no LLM-inferred answers were needed. If any condition fails, the application falls to the human-review tier: agent pauses at Submit, human confirms.

This replaces AGENTS.md's blanket "V1 requires explicit human approval before every final submit" with a narrower, evidence-based gate. AGENTS.md's Core Policies section is rewritten to make the three-tier model the new default; the "prefer stricter" Safety Overrides clause remains but applies to runtime overrides that *tighten* the policy, not loosen it.

### Apply surfaces: Indeed Easy Apply + external ATS redirects
Both are in scope for v1. Easy Apply is a single form schema on indeed.com. ATS redirects reuse the existing Greenhouse/Lever discovery muscle but each ATS needs its own playbook branch. Company-direct pages are out of scope — they fall back to the existing "tailored draft, human applies" flow.

### Work authorization: Fill in profile now
Add `work_authorization` and `sponsorship_required` fields to `profile/normalized/candidate-profile.json` as part of this feature. This converts the two most-asked Indeed screening questions from "escalate" to "supported fact" and unblocks the tier-1 auto-submit path. Profile goes from 94% to 100%.

### Session strategy: Saved Chrome profile
A dedicated Chrome profile (e.g., `~/Library/.../Chrome/Profile Job-Hunt`) pre-authenticated to indeed.com with "remember me" set. The Chrome extension uses this profile. No credentials in the repo; no login automation; no 2FA logic. Session expiry is surfaced by a preflight check that stops the run with "please re-auth in the Job-Hunt Chrome profile."

### Unknown-question handling: Answer bank + flagged LLM fallback
New artifact `data/answer-bank.json` stores curated question→answer pairs with `source: curated|inferred` and `reviewed: true|false`. Resolution order:
1. If question matches a bank entry with `source: curated, reviewed: true`, use it → supported fact → tier-1 eligible.
2. If no match, LLM generates an answer from profile + job description, writes it to the bank with `source: inferred, reviewed: false`, and drops this application into the human-review tier.
3. User reviews inferred entries via the `docs/reports/answer-bank-pending.md` report and edits `data/answer-bank.json` by hand to flip `reviewed: true` (or delete/revise the answer). Promoting an entry to curated+reviewed means the next posting with the same question is tier-1 eligible.

The bank ships with a **seeded starter set** at feature launch: work-authorization / sponsorship / relocation / start-date / minimum-salary yes-nos, years-of-experience variants, and a templated "why this role/company" answer. This front-loads the review work so the first real batch-10 run already has non-zero tier-1 coverage.

The bank is a compounding asset: every application either hits existing curated entries (free automation) or generates new inferred entries (one review, permanent unlock).

### MVP slice: One Indeed posting, full pipeline including discovery
First working demo: system takes an Indeed search URL, discovers postings, picks top-1 by fit score, tailors resume, fills form via Chrome extension, pauses at Submit for human click (first run is implicitly human-review tier — we're establishing the pipeline, not yet trusting tier-1). Success criterion: one real Indeed application submitted with a full audit record in `data/applications/`.

### Policy reversal scope: Indeed-only carve-out
Add `config/allowed_login_walled_domains.yaml` listing `indeed.com`. Ingestion and discovery check this list before returning `login_wall` error. Default remains block; LinkedIn and others stay blocked. Policy is narrow and reversible.

### Close-the-loop: Gmail-driven confirmation and status tracking
The application lifecycle extends past Submit:
- **Confirmation**: After submit, the agent polls Gmail (via the already-available Gmail MCP) for an Indeed/ATS confirmation email. Parsing the email is the definitive "submitted" signal. URL change and in-page confirmation text serve as *provisional* signals until the email arrives.
- **Status tracking**: Subsequent rejection / interview-request / offer emails update `data/applications/{draft_id}/status.json` and feed into `apps-dashboard` analytics.
- **Timeout handling**: If the confirmation email doesn't arrive within a configurable window (e.g., 30 minutes), the application is flagged `confirmation_email_timeout` for triage — did it submit or silently fail?

### Inter-application pacing
Batch-10 enforces a jittered 60-120s delay between applications to avoid Indeed anti-bot detection. The existing `DomainRateLimiter` (500ms) is appropriate for discovery, not apply.

### Artifact shape (preliminary)
- `data/applications/{draft_id}/draft.json` — lead, selected variant, resume path, cover letter, tailored answers (existing, extend)
- `data/applications/{draft_id}/plan.json` — ordered list of form fields the agent expects to fill, with source + confidence per field
- `data/applications/{draft_id}/attempt.json` — what actually happened per submit attempt (URL, tabs opened, checkpoints reached, final state, screenshots captured)
- `data/applications/{draft_id}/status.json` — post-submit lifecycle: confirmation state, rejection/interview/offer events
- `data/applications/{draft_id}/checkpoints/` — screenshot PNGs at: landing page, form filled (pre-submit), post-submit confirmation
- `data/answer-bank.json` — question→answer map with source + reviewed metadata
- `config/allowed_login_walled_domains.yaml` — narrow carve-out list; ships with `indeed.com` as the only entry

## Resolved Questions

1. **Tier-1 gate wording** → Curated answer-bank entries (`source: curated, reviewed: true`) count as supported facts. The bank is a first-class compounding asset; reviewing an inferred entry and promoting it to curated directly unlocks tier-1 for the next posting with that question.

2. **Answer-bank seed content** → Seed with common screener templates at feature launch: years-of-experience variants, work authorization, sponsorship, willingness to relocate, start-date availability, minimum salary, and a "why this role/company" template. Empty-start was rejected — the upfront writing is worth the faster path to tier-1 auto-submit.

3. **AGENTS.md update approach** → Rewrite the Core Policies section to describe the three-tier approval model as the new source of truth. The "prefer stricter" Safety Overrides clause stays, but the *default* is now the tier policy, not the blanket V1 approval gate. Runtime overrides can tighten (e.g., force human-review for all) but not loosen.

4. **Post-apply follow-up** → In scope. The application lifecycle isn't complete until the confirmation email is parsed and status tracked. This includes:
    - Parsing Indeed confirmation emails via Gmail MCP as the definitive "submitted" signal
    - Tracking rejection/interview/offer responses to populate `apps-dashboard`
    - Updating application status fields in `data/applications/{draft_id}/status.json`
   Scope is larger but the feature is "end-to-end autonomous apply," not "fire-and-forget."

5. **Inter-application pacing** → Moderate: 60-120s jittered delay between applications in a batch. Adaptive back-off on failure signals (Cloudflare challenges, rate-limit responses, 403s) is a stretch goal, not v1.

6. **Flagged-entry review UX** → Markdown report at `docs/reports/answer-bank-pending.md`, edit `data/answer-bank.json` by hand to flip `reviewed: false → true`. No new interactive CLI surface. Fits the repo's "artifacts are editable markdown/JSON" style.

## Deferred to Planning Phase

These are implementation-detail questions whose answers belong in the plan document, not in strategic brainstorming.

- **Batch orchestration shape**: 10 sequential sub-agent sessions vs one long session vs worktree-per-posting. Affects tab-budget accounting (10 soft / 15 hard is per-session). Plan will specify the orchestration pattern.

- **Playbook granularity**: One playbook with branches vs per-surface playbooks (`playbooks/application/indeed-easy-apply.md`, `playbooks/application/greenhouse.md`, etc.). The existing `generic-application.md` is a six-line stub.

- **Submission confirmation signal hierarchy**: Email is the definitive signal (per the "close the loop" decision), but URL-change and in-page-text should serve as provisional confirmation until the email arrives. Plan will specify the exact state machine.

- **`ApplicationError.error_code` enum**: Need a frozen enum of failure modes (`session_expired`, `tab_budget_exhausted`, `form_field_unresolved`, `submit_button_missing`, `cloudflare_challenge`, `unknown_question_escalation`, `confirmation_email_timeout`, etc.) per the repo's structured-error convention. Enumeration happens during plan-phase design.

## What This Unlocks

Once the MVP slice lands:
- Fulfills the original "apply to top 10" workflow on the most common job board.
- Indeed session state (logged-in Chrome profile) becomes a reusable asset for other browser-driven features.
- The answer bank compounds over time — each application makes the next cheaper.
- The tier policy becomes a reusable pattern for any future auto-action the repo takes (follow-up emails, recruiter responses, etc.).
