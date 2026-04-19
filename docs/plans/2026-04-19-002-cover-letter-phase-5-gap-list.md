---
title: "gap list: Cover-letter lanes Phase 5 normalized fragment layer"
type: gap_list
status: active
date: 2026-04-19
origin: docs/plans/2026-04-18-001-feat-cover-letter-lanes-plan.md
---

# gap list: Cover-Letter Lanes Phase 5 Normalized Fragment Layer

## Purpose

This document isolates the remaining work for Phase 5 of the completed cover-letter-lanes plan.

Phases 0-4 are already shipped. The missing work is the later normalized fragment layer described in:

- `docs/plans/2026-04-18-001-feat-cover-letter-lanes-plan.md`

The goal of Phase 5 is to move from:

- first-slice reuse of normalized highlights, skills, question-bank entries, and a tiny project-note allowlist

to:

- a structured, normalized fragment layer that is safer to reuse and easier to audit than raw freeform profile text

## What Is Already Done

The repo already has:

- lane-aware cover-letter generation in `src/job_hunt/generation.py`
- `--lane` CLI support in `src/job_hunt/core.py`
- lane-aware ATS handling in `src/job_hunt/ats_check.py`
- lane-focused tests in `tests/test_cover_letter_lanes.py`
- optional generated-content metadata such as:
  - `lane_id`
  - `lane_source`
  - `lane_rationale`
  - `generation_warnings`
  - `company_facts_used`

## What Phase 5 Still Needs

Phase 5 is not implemented yet. The missing work falls into five buckets:

1. Normalize reusable cover-letter fragments into the candidate profile.
2. Add schema support for the fragment layer.
3. Add extraction-time safety and review gates.
4. Teach generation to consume safe fragments.
5. Add tests proving risky fragments are excluded by default.

## Missing Deliverables

### 1. Candidate-Profile Fragment Field

Missing:

- a new optional normalized-profile field such as `cover_letter_fragments`

Expected shape:

- array of fragment objects

Each fragment should carry enough metadata to be auditable and safely filtered before generation.

Minimum fields:

- `fragment_id`
- `fragment_type`
- `text`
- `source_document_ids`
- `source_excerpt`
- `lane_affinities`
- `candidate_fact_only`
- `company_specific_risk`
- `stale_company_risk`
- `reviewed_by_human`
- `reusable_for_generation`

Nice-to-have fields:

- `tags`
- `notes`
- `derived_from_document_type`

### 2. Normalization Logic

Missing:

- extraction logic in the profile-normalization path that derives reusable fragments from candidate-authored raw documents

Likely source files:

- `profile/raw/cover-letter.txt`
- `profile/raw/cover-letter2.txt`
- `profile/raw/question-examples.txt`
- `profile/raw/preferences.md`
- `profile/raw/job-hunt.md`
- `profile/raw/ai-company-os.md`

Expected behavior:

- extract candidate-side narrative fragments
- do not automatically trust company-specific prose
- mark risky fragments instead of silently dropping all context
- require explicit review for fragments originating from legacy raw cover letters

### 3. Extraction-Time Safety Rules

Missing:

- a quarantine/review model at normalization time for risky fragments

The fragment layer should enforce:

- raw cover-letter fragments are not generation-eligible by default
- company-specific fragments are not generation-eligible unless proven generic or explicitly reviewed
- stale-company-risk fragments are excluded from generation by default
- human review is required before legacy raw-cover-letter fragments can become reusable

Expected enforcement fields:

- `stale_company_risk: none|low|high`
- `company_specific_risk: none|low|high`
- `reviewed_by_human: true|false`
- `reusable_for_generation: true|false`

### 4. Generator Consumption Of Fragments

Missing:

- fragment-aware selection in `src/job_hunt/generation.py`

Expected behavior:

- prefer safe structured fragments over ad hoc raw-source reuse
- include fragments only when:
  - `reusable_for_generation` is true
  - `reviewed_by_human` is true when required
  - fragment risk is acceptable for the current use
- continue to source company-specific claims only from lead/company research
- use fragments for candidate narrative shape, motivation, proof-point framing, and closing language

Expected generated-content additions, if helpful:

- `selected_fragment_ids`
- `fragment_exclusion_reasons`

These should remain optional.

### 5. Tests

Missing:

- normalization tests for fragment extraction
- schema tests for the new candidate-profile field
- generation tests proving fragments are preferred when safe
- generation tests proving risky fragments are skipped
- regression tests for stale-company language laundering through fragments

## Likely Files To Change

### Primary Code

- `src/job_hunt/profile.py`
- `src/job_hunt/core.py`
- `src/job_hunt/generation.py`
- `src/job_hunt/ats_check.py` only if fragment-driven warnings need relay behavior

### Schemas

- `schemas/candidate-profile.schema.json`
- possibly `schemas/generated-content.schema.json` if fragment usage metadata is written out

### Tests

- `tests/test_pipeline.py`
- `tests/test_generation.py`
- `tests/test_cover_letter_lanes.py`
- likely a new file such as `tests/test_profile_fragments.py`

### Data / Fixtures

- `profile/raw/*` fixtures already exist and can drive realistic extraction tests
- `profile/normalized/candidate-profile.json` will change after re-normalization

## Suggested Execution Plan

### Step 1: Add Schema And Normalized Data Shape

- add optional `cover_letter_fragments` to `schemas/candidate-profile.schema.json`
- keep it optional for compatibility
- add tests showing old profiles still validate

Acceptance:

- old candidate profiles validate unchanged
- profiles with fragment arrays also validate

### Step 2: Implement Fragment Extraction In Normalization

- extend profile normalization in `src/job_hunt/profile.py`
- derive fragments from a narrow allowlist of candidate-authored raw sources
- tag each fragment with risk and review metadata

Acceptance:

- normalization produces fragment objects for relevant raw materials
- cover-letter-derived fragments default to `reviewed_by_human: false` and are not generation-eligible
- explicitly generic motivation/work-style fragments can be eligible if they are clearly candidate-only

### Step 3: Add Fragment Eligibility Filter

- implement a single filtering path that decides whether a fragment is usable for generation
- keep this filtering deterministic and independently testable

Acceptance:

- high-risk fragments are excluded by default
- missing review blocks reuse where required
- company-specific fragments never unlock unsupported company claims

### Step 4: Teach Generation To Use Safe Fragments

- extend lane-aware evidence selection in `src/job_hunt/generation.py`
- prefer safe fragments for candidate narrative sections
- keep current question-bank/highlight logic as fallback

Acceptance:

- generation can use structured fragments when available
- generation still works when no fragments exist
- company-specific language remains Tier 1 only

### Step 5: Add Regression And Safety Tests

- stale-company leakage through fragments
- high-risk fragment skip behavior
- reviewed vs unreviewed fragment behavior
- fragment metadata traceability in outputs if persisted

Acceptance:

- risky fragments are skipped by default
- safe fragments improve narrative variety
- no stale target-company names leak into final letters

## Concrete Gaps Versus The Phase 5 Plan

Below is the direct gap mapping against the Phase 5 intent.

### Planned But Missing

- `cover_letter_fragments` normalized profile field
- fragment extraction and tagging
- fragment review gate
- generator preference for structured fragments
- fragment quarantine behavior
- fragment-specific tests

### Planned And Still Intentionally Deferred

- full analytics on fragment usage
- any requirement to make fragment metadata mandatory in schemas
- any freeform raw-document retrieval at generation time

## Recommended Defaults

If another Codex instance executes this work, these defaults are recommended:

- keep `cover_letter_fragments` optional
- keep all fragment filtering deterministic
- default `reviewed_by_human` to `false` for fragments extracted from raw cover-letter drafts
- default `reusable_for_generation` to `false` when:
  - `stale_company_risk == "high"`
  - `company_specific_risk != "none"`
  - the fragment came from legacy raw cover-letter files and has not been reviewed
- keep company-specific language sourcing unchanged:
  - only `lead` and `company_research` may authorize company claims

## Open Decisions For Execution

- Should fragment extraction include only candidate-authored prose, or also transformed question-bank answers?
- Should `question-examples.txt` produce fragments directly, or should it remain question-bank-first with fragment extraction only for clearly generic narrative sections?
- Should fragment usage be written into generated-content records immediately as `selected_fragment_ids`, or should that wait until the fragment layer proves useful?
- Should human review be modeled as a boolean field only, or as a small audit structure with reviewer and reviewed_at?

## Recommended Acceptance Summary

Phase 5 should be considered done when:

- normalized profiles can carry safe structured cover-letter fragments
- risky fragments are quarantined or excluded by default
- generation prefers safe fragments when present
- generation still works without fragments
- no stale-company or unsupported company-fact regression is introduced
- schema compatibility remains intact for older profiles and older generated artifacts
