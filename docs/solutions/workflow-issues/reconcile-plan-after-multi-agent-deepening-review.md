---
title: Reconcile split-brain feature plan where simplification decisions were not applied to schemas, code, and deliverables
date: 2026-04-16
module: plan_review
problem_type: workflow_issue
component: content_generation_plan
symptoms:
  - 8 simplification decisions declared in Enhancement Summary contradicted by schema definitions, code blocks, deliverables, and acceptance criteria in the same document
  - .gitignore lacked PII exclusions for generated data directories containing candidate name, email, phone, salary, and work authorization status
  - No agent-native variant selection flag existed; output formats assumed human-only consumption
  - Two independent status systems (lead.status and application.current_stage) with no documented relationship
  - Schema blocks retained removed fields (profile_snapshot_hash, metadata, follow_up_draft_path, combined_recommendation)
root_cause: The multi-agent deepening pass produced correct simplification decisions in Enhancement Summary prose but did not propagate those decisions into concrete artifacts (JSON schemas, Python code blocks, deliverable checklists, acceptance criteria, error propagation sections) that an implementer would follow
tags:
  - workflow
  - plan-review
  - split-brain
  - simplification
  - security
  - agent-native
  - schema-consistency
  - data-integrity
  - gitignore
  - pii
severity: high
---

# Reconcile split-brain feature plan after multi-agent deepening and review

## Problem

A feature plan for 9 new features (content generation, application tracking, company research, follow-up system) was created, then deepened by 8 research agents, then reviewed by 8 more review agents. The review discovered that the deepening pass added correct simplification decisions to the Enhancement Summary but did not propagate those decisions into the schemas, code blocks, deliverables, acceptance criteria, and error propagation sections throughout the plan. An implementer following the code blocks would build the complex version; one following the prose would build the simple version.

Additionally, three other categories of issues were discovered:
- **Security:** `.gitignore` had zero data directory exclusions despite the repo containing real PII (name, email, phone, salary)
- **Agent-native parity:** No CLI flag for variant selection, query commands output human text instead of JSON
- **Data integrity:** Two unsynchronized status systems, missing backward compatibility constraints, schema fields not enum-constrained

## Root Cause

When a plan goes through multiple review rounds, each round produces directives (simplify X, remove Y, add Z) as prose annotations. These annotations are appended to the document as new paragraphs and summary tables WITHOUT mechanically walking through every concrete artifact and reconciling it. The failure is not in the review quality (6 of 8 agents independently flagged the same root cause) but in the absence of a propagation step between "decide" and "done."

The specific contradiction pattern: The Enhancement Summary table declared 8 simplifications. Each was correct and well-reasoned. But the JSON schemas, Python code blocks, deliverable checklists, acceptance criteria, error propagation sections, and test lists still contained the pre-simplification versions.

## Solution

### The 8 contradictions reconciled

| Decision | Where it was applied (prose) | Where it was NOT applied (artifacts) | Fix |
|---|---|---|---|
| Drop `config/generation.yaml` | Enhancement Summary | Directory tree, Phase 2 deliverables | Removed from both |
| Replace `RESUME_VARIANT_STYLES` dict with string constants | Enhancement Summary | Phase 2 code block (full nested dict) | Replaced with 3 string constants + `VARIANT_BOOST_PHRASES` |
| Remove `profile_snapshot_hash` | Enhancement Summary | Schema required array, acceptance criteria, error propagation, Phase 1 tests | Removed from all 4 locations |
| Drop `combined_recommendation` | Enhancement Summary | Phase 4 output example, downgrade logic, test, deliverable | Removed from all 4 locations |
| Flatten `selected_accomplishments` to string list | Enhancement Summary | Schema (object array with relevance_score) | Changed to `{ "type": "array", "items": { "type": "string" } }` |
| Remove `metadata` catch-all | Enhancement Summary | Schema property | Removed |
| Remove `follow_up_draft_path` | Enhancement Summary | Application-status schema | Removed |
| Replace ATS regex framework with keyword dict | Enhancement Summary | Phase 3 code block (full regex framework) | Replaced with `ATS_KNOCKOUT_KEYWORDS` keyword dict |

### Additional fixes (from review, not from deepening contradictions)

- **`.gitignore` PII protection:** Added exclusions for `profile/raw/`, `profile/normalized/`, `data/generated/`, `data/companies/`, `data/applications/`, `data/leads/`, `data/runs/`, `docs/reports/*-report.md`, `examples/results/`, `*.tmp`
- **Agent variant selection:** Added `--resume-variant <content-id>` flag to `build-draft`
- **JSON output for query commands:** All query commands (`check-follow-ups`, `list-applications`, `check-status`, `check-integrity`) default to JSON arrays on stdout
- **Status relationship documented:** `lead.status` = discovery phase, `application.current_stage` = lifecycle. Different purposes, no synchronization needed.
- **Atomic `write_json`:** Uses `os.replace()` with try/finally cleanup instead of `Path.rename()`
- **Schema backward compatibility:** All new fields on existing schemas explicitly OPTIONAL. `from_stage`/`to_stage` constrained to stage enum. Score fields use `"type": "number"`.
- **`generation_tokens()`:** Preserves 2-char terms (AI, ML, Go, UI) that `core.tokens()` drops
- **`SENSITIVE_KEYWORDS` scope clarified:** Protects browser attempt payloads only, NOT generated content PII. `.gitignore` is the PII protection.
- **`_build_answers` preserved:** New `generate-answers` is a separate code path; existing function untouched for backward compatibility

### Key implementation pattern

The reconciliation was a single systematic pass: read each simplification decision, grep the document for every occurrence of the concept, update each occurrence to match the decision. Bottom-to-top reading order helped catch contradictions that top-to-bottom reading missed due to narrative anchoring.

## Why This Worked

- It treated the plan as a codebase with a consistency invariant, not as a document that merely needs to "read well"
- The single-pass approach was faster and less error-prone than fixing contradictions one at a time as they were discovered
- The todo files (12 findings) served as a checklist to verify completeness

## Prevention

### For plan reconciliation after review rounds

1. **Decision ledger with affected-section checklist.** Every scope change gets a ledger entry listing which sections are affected. The plan is not done until every affected section is checked off.
2. **Canonical source rule.** Pick one representation as truth for each concept. If the deliverable list says "3 schemas" and a code block shows 5, the deliverable list wins.
3. **Reverse-reading pass.** After any review round, read bottom-to-top to defeat anchoring bias.
4. **Contradiction scan as a gate.** Run a dedicated pass whose sole job is finding contradictions, separate from quality or completeness review.

### For security in PII-handling repos

1. **Security-first repo template.** `.gitignore` with PII exclusions is part of the first commit, not an afterthought.
2. **Pre-commit hook for sensitive patterns.** Scan staged files for phone numbers, email addresses, salary figures.
3. **Data directory convention.** All PII in one excluded directory tree. No scatter.

### For agent-native parity

1. **JSON-first, human-second.** Every command outputs structured JSON by default.
2. **Flag completeness rule.** Any decision a human makes interactively must have a corresponding CLI flag.
3. **Agent persona in acceptance criteria.** At least one criterion per feature written from the agent's perspective.

### General principle

These failures share a common pattern: **the thing that gets built diverges from the thing that was decided, and there is no mechanism to detect the divergence.** The prevention pattern: make decisions in exactly one place, mechanically verify consistency, make the safe path the default, and test with the actual consumer.

## References

- `docs/plans/2026-04-15-002-feat-content-generation-and-tracking-plan.md` -- the reconciled plan
- `docs/plans/2026-04-15-001-feat-agent-first-job-hunt-system-plan.md` -- the original system plan
- `docs/solutions/security-issues/design-secret-handling-as-a-runtime-boundary.md` -- related security pattern
- `docs/solutions/workflow-issues/harden-profile-normalization-signal-selection.md` -- related trust-boundary pattern
- `todos/001-complete-p1-reconcile-simplification-contradictions.md` through `012-complete-p3-missing-tests.md` -- the 12 findings
