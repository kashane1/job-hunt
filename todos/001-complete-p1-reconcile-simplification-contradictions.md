---
status: pending
priority: p1
issue_id: "001"
tags: [code-review, architecture, plan-quality]
dependencies: []
---

# Reconcile 8 simplification decisions not applied to schemas/code blocks

## Problem Statement

The plan went through a deepening pass that produced correct simplification decisions, but those decisions were declared in the Enhancement Summary WITHOUT being applied to the schema definitions, code blocks, deliverable checklists, acceptance criteria, and error propagation sections. An implementer following the code blocks will build the complex version; an implementer following the prose will build the simple version.

This was flagged independently by 6 of 8 review agents (architecture, python, simplicity, data integrity, agent-native, performance).

## Findings

**8 specific contradictions found:**

1. `config/generation.yaml` -- Simplification says DROP. Directory tree (line 78) and Phase 2 deliverables (line 623) still list it.
2. `RESUME_VARIANT_STYLES` -- Simplification says "simple style strings." Phase 2 code (lines 473-492) defines full structured dict.
3. `profile_snapshot_hash` -- Simplification says DROP. Schema (line 169) still has it as REQUIRED. Acceptance criteria (line 1093) and error propagation (line 1063) still reference it. Phase 1 test (line 437) tests a removed feature.
4. `combined_recommendation` -- Simplification says DROP. Phase 4 (lines 821-825) reintroduces it with downgrade logic.
5. `selected_accomplishments` -- Simplification says "flat string list." Schema (lines 173-185) still uses object array with `relevance_score`.
6. `metadata` catch-all -- Simplification says REMOVE. Schema (line 188) still has it.
7. `follow_up_draft_path` -- Simplification says REMOVE. Status schema (line 238) still has it.
8. `ATS_KNOCKOUT_QUESTIONS` -- Simplification says "keyword dict." Phase 3 code (lines 647-685) defines regex framework.
9. `VALID_TRANSITIONS` -- Simplification says "terminal-only check." Phase 1 code (lines 390-399) defines full matrix.

Additionally: `follow_up_schedule_days` in runtime.yaml (line 936) contradicts the "hardcode in module" decision (line 958).

## Proposed Solutions

### Option 1: Single reconciliation pass (Recommended)

**Approach:** Go through every schema block, code block, deliverable checklist, acceptance criterion, and error propagation reference in the plan. Update each to match the simplification decision.

**Specific changes:**
- Remove `config/generation.yaml` from directory tree and deliverables
- Replace `RESUME_VARIANT_STYLES` dict with 3 string constants
- Remove `profile_snapshot_hash` from schema required array, properties, acceptance criteria, error section, and test list
- Remove `combined_recommendation` from Phase 4 output example, downgrade logic, test, and deliverable
- Change `selected_accomplishments` schema to `{ "type": "array", "items": { "type": "string" } }`
- Remove `metadata` property from generated-content schema
- Remove `follow_up_draft_path` from application-status schema
- Replace `ATS_KNOCKOUT_QUESTIONS` regex framework with keyword dict
- Replace `VALID_TRANSITIONS` with `TERMINAL_STAGES` + simple validation
- Remove `follow_up_schedule_days` from runtime.yaml additions
- Standardize `variant_style` parameter type to `str` everywhere

**Effort:** 1-2 hours (plan editing only, no code)
**Risk:** Low -- only changes the plan document

## Recommended Action

Option 1. This is a plan document fix, not a code change. Must be done before any implementation begins.

## Acceptance Criteria

- [ ] Every schema block matches the simplification decision in the Enhancement Summary table
- [ ] Every code block matches the simplification decision
- [ ] Every deliverable checklist matches
- [ ] Every acceptance criterion matches
- [ ] Every error propagation reference matches
- [ ] No internal contradictions remain when reading the plan top to bottom

## Work Log

### 2026-04-16 - Discovery

**By:** 8 parallel review agents (architecture, python, simplicity, security, performance, data-integrity, agent-native, learnings)

**Actions:**
- All agents independently identified the same root cause: simplification decisions applied to prose but not to implementation artifacts
- Architecture review found 6 P1 contradictions
- Python review found 7 P1 contradictions
- Simplicity review found 8 P1 inconsistencies
- Data integrity review found 4 P1 contradictions overlapping with above
