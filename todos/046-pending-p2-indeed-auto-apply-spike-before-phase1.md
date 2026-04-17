---
status: pending
priority: p2
issue_id: 046
tags: [code-review, spike, indeed-auto-apply]
dependencies: []
---

# Pre-Phase-1 spike: validate MCP tool surface + Indeed form shape before committing to the plan

## Problem Statement

The plan at `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md` makes concrete assumptions about:

1. The `mcp__Claude_in_Chrome__*` tool schemas (navigate, find, form_input, file_upload, click, get_page_text) — framework-docs research confirmed no public docs exist; tools must be discovered empirically.
2. The Indeed Easy Apply form field shape in 2026 — best-practices research surfaced the AI Recruiter / Smart Screening change but didn't catalogue exact field taxonomies.
3. Indeed's anti-bot behavior at the proposed pacing (log-normal 60-300s) — untested against real Indeed Cloudflare.
4. Gmail MCP tool response shape for `gmail_search_messages` — community-server patterns vary.

Phase 1b commits to schema shapes (`application-plan.schema.json.fields[]`) that presume a stable Easy Apply field taxonomy. If real Indeed diverges meaningfully, Phase 4/5 will force schema churn.

## Findings

- Enhancement Summary items 24 (AI Recruiter), 25 (DOCX), 27 (Gmail cursor) are all based on inference from 2026 research, not empirical testing.
- Framework-docs research explicitly recommended "empirical discovery" for MCP tool surfaces.
- The plan ships with several unvalidated assumptions about the real UI/API surfaces.

## Proposed Solutions

### Option 1: 1-day spike before Phase 1 starts (Recommended)
Dedicate 1 day (not a full phase) to:
- Log every `mcp__Claude_in_Chrome__*` tool's response shape on a single real Indeed posting.
- Catalogue the Easy Apply field taxonomy on 5 real Indeed postings (screenshot + field-by-field list).
- Confirm or refute the AI Recruiter widget detection pattern (class names, aria-labels).
- Confirm Gmail MCP query DSL behavior (`newer_than:` accepted, `since:` rejected, `OR` uppercase, etc.).
- Probe Indeed's anti-bot behavior with 3 applications at 90s pacing; document any challenge pages.
- Write findings to `docs/solutions/indeed-auto-apply-spike-findings.md`.

- Pros: De-risks 5-7 days of downstream Phase 4/5 work.
- Cons: 1 day not shipped as user-facing value.
- Effort: Small (1 day).
- Risk: Low.

### Option 2: Skip spike; accept schema churn risk
Proceed with Phase 1 as planned; discover deltas in Phase 4/5 and iterate.

- Pros: No delay.
- Cons: Schema changes in Phase 4 force `application-plan.schema.json` churn and re-test.
- Risk: Medium.

### Option 3: Defer spike to after Phase 4 (Python foundation in place)
Phase 4 ships the prep pipeline; spike happens before Phase 5 playbook writing.

- Pros: Spike has Python helpers to use.
- Cons: Phase 4 schemas still based on unvalidated assumptions.
- Risk: Medium.

## Recommended Action

Option 1. Spike in the week before Phase 1a. Findings inform the "Artifact Shapes" section (plan.json.fields[] in particular).

## Technical Details

- Output: `docs/solutions/indeed-auto-apply-spike-findings.md` (institutional learning per `/workflows:compound` pattern)
- Tool: Chrome with the Claude extension, logged into Indeed with a test account
- No code changes required by the spike itself

## Acceptance Criteria

- [ ] Catalogue of `mcp__Claude_in_Chrome__*` tool responses observed on Indeed posting
- [ ] Field taxonomy from 5 real Indeed Easy Apply postings
- [ ] AI Recruiter detection signal confirmed or refuted
- [ ] Gmail MCP query DSL behavior confirmed for proposed patterns
- [ ] Anti-bot behavior documented at 90s pacing (challenge page present? after how many applications?)
- [ ] `docs/solutions/indeed-auto-apply-spike-findings.md` committed

## Work Log

- 2026-04-17: Created from technical-review pass on indeed-auto-apply plan.

## Resources

- Plan: `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md`
- Claude for Chrome: https://code.claude.com/docs/en/chrome
- Framework-docs research output (in conversation history)
