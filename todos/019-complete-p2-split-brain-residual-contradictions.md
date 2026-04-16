---
status: pending
priority: p2
issue_id: "019"
tags: [code-review, split-brain, plan-quality, batch-2]
dependencies: []
---

# Residual split-brain contradictions between Enhancement Summary and plan body

## Problem Statement

The deepening pass added an Enhancement Summary listing 18 fixes. Most were applied correctly into schemas/code/deliverables, but the architecture and learnings reviewers found residual contradictions where the prose claim doesn't match the implementation block.

This is exactly the split-brain pattern from batch 1's `reconcile-plan-after-multi-agent-deepening-review.md` solution doc.

## Findings

### 1. Interaction Graph still shows generation.py calling ats_check

Plan Interaction Graph section shows:
```
generate-resume → generation.py::generate_resume_variants
    → calls ats_check.run_ats_check (automatic)
```

But the architecture inversion (Enhancement Summary #6, Phase 3 deliverable) says **CLI orchestrates, generation.py does NOT import ats_check**. The Interaction Graph should say "core.py CLI dispatch calls ats_check.run_ats_check after generate_resume_variants returns."

### 2. `RESUME_MAX_PAGES` vs `RESUME_MAX_PAGES_DEFAULT`

- Enhancement Summary #7: uses `RESUME_MAX_PAGES = 1`
- Code: uses `RESUME_MAX_PAGES_DEFAULT = 1`

Minor but should be consistent.

### 3. `redirect_blocked` in Enhancement Summary #8 but not in error-code enumeration

- Enhancement Summary #8 lists `redirect_blocked` as a structured error code
- Phase 2 deliverable enumeration of `IngestionError.error_code` values does NOT include `redirect_blocked`
- Acceptance criterion expects it; code doesn't emit it (overlap with todo 013)

### 4. `pdf_fetch_blocked` in code but not in Enhancement Summary

- `_safe_url_fetcher` raises `PdfExportError` with `error_code="pdf_fetch_blocked"`
- Enhancement Summary #8 lists error codes for both exceptions but omits `pdf_fetch_blocked`

### 5. Test name mismatch

- Docstring references: `test_markdown_to_html_covers_all_generated_shapes`
- Test list (plan deliverable): `test_markdown_to_html_handles_known_generated_shapes`

Pick one name.

### 6. `test_generate_resume_attaches_ats_check_when_enabled`

Under the inverted architecture (CLI orchestrates, generation.py doesn't know about ats_check), this test name is misleading. Rename to `test_cli_generate_resume_attaches_ats_check` to reflect that the CLI layer attaches it, not `generate_resume_variants`.

### 7. Architecture constraint not listed as testable deliverable

Plan prose says "generation.py does NOT import ats_check." This is the most important architectural invariant, but it's not listed as a testable deliverable. Add:
```
- [ ] Architecture constraint: `grep "from .ats_check" src/job_hunt/generation.py` returns no matches (enforced by test)
```

### 8. `AggregatedRow` TypedDict promised but not shown

- Deliverable says "`AggregatedRow` `TypedDict` locks the aggregator output shape"
- Code in plan still shows `def build_aggregator(data_root: Path) -> list[dict]:`
- The type hint was never updated in the plan body

## Proposed Solutions

### Option 1: Systematic reconciliation pass (Recommended)

Go through the Enhancement Summary line by line and verify every claim has a matching artifact in schemas/code/deliverables/acceptance criteria. Fix each mismatch. This is the exact remediation pattern from batch 1's `reconcile-plan` solution.

Specific edits:
- Update Interaction Graph to show CLI orchestrating ATS
- Align `RESUME_MAX_PAGES_DEFAULT` everywhere
- Add `redirect_blocked` to Phase 2 error code list
- Add `pdf_fetch_blocked` to Enhancement Summary #8
- Pick one test name (`covers` or `handles`) and use consistently
- Rename `test_generate_resume_attaches_ats_check_when_enabled` to `test_cli_generate_resume_attaches_ats_check`
- Add "generation.py does not import ats_check" as explicit testable deliverable
- Update `build_aggregator` signature in plan to return `list[AggregatedRow]`

**Effort:** Small (plan edits)
**Risk:** Low

## Recommended Action

Option 1. These are the same category of issues batch 1's reconciliation solution was written to prevent. One more reconciliation pass and the plan is clean.

## Acceptance Criteria

- [ ] Interaction Graph reflects the inverted ATS hook architecture
- [ ] `RESUME_MAX_PAGES_DEFAULT` naming consistent throughout
- [ ] Error code enumeration includes all codes actually raised (`redirect_blocked`, `pdf_fetch_blocked`, `decompression_bomb`)
- [ ] All test names are consistent between docstrings and deliverable lists
- [ ] Architecture invariant (generation.py no-import) is a testable deliverable
- [ ] `AggregatedRow` TypedDict shown in plan code, not just deliverable

## Work Log

### 2026-04-16 - Discovery

**By:** architecture-strategist, learnings-researcher

**Actions:**
- Architecture reviewer cross-checked Enhancement Summary items against plan body — found 8 residual contradictions
- Learnings reviewer confirmed this matches the exact pattern batch 1's reconciliation solution warned about
