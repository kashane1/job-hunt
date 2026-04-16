---
status: pending
priority: p2
issue_id: "007"
tags: [code-review, data-integrity, architecture]
dependencies: []
---

# Ensure new schema fields are optional for backward compatibility

## Problem Statement

The plan adds fields to `application-draft.schema.json` (resume_content_ids, selected_resume_content_id, etc.) and `lead.schema.json` (company_research_id, application_status_path) without specifying they are optional. If added to `required`, existing artifacts break validation.

## Findings

- Current `selected_assets` requires `resume_document_id` and `cover_letter_document_id`
- New content ID fields must NOT be added to `required`
- All code reading new fields must use `.get()` with defaults
- `company_fit_score` should use `"type": "number"` not `"type": "integer"` to match conventions
- `from_stage`/`to_stage` in transitions should be constrained to the stage enum

## Proposed Solutions

### Option 1: Explicitly document optionality (Recommended)

**Approach:** In the plan, add a clear statement: "All new fields on existing schemas are OPTIONAL. Never add to `required` arrays." Constrain `from_stage`/`to_stage` to enum. Use `"type": "number"` for scores.

**Effort:** Small (plan edit + implementation convention)
**Risk:** Low

## Acceptance Criteria

- [ ] Plan explicitly states new fields are optional
- [ ] `from_stage` and `to_stage` use same enum as `current_stage`
- [ ] `company_fit_score` uses `"type": "number"`
- [ ] All code accessing new optional fields uses `.get()` with defaults

## Work Log

### 2026-04-16 - Discovery

**By:** Architecture reviewer, data integrity reviewer, pattern reviewer
