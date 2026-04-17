---
status: pending
priority: p2
issue_id: "034"
tags: [code-review, architecture, batch-3, error-handling]
dependencies: []
---

# Introduce `utils.StructuredError` base class before IngestionError/DiscoveryError hierarchies diverge further

## Problem Statement

`IngestionError` (batch 2), `PdfExportError` (batch 2), and the new `DiscoveryError` (batch 3) all mirror the exact same shape: `message`, `error_code`, `url`, `remediation`, `to_dict()`. They share no parent other than `ValueError`. CLI error handlers in `core.py` must catch each specifically, or catch `ValueError` (too broad).

Adding the base class LATER is backward-incompatible for any code catching on the specific types. It's cheap to add now, expensive to retrofit once more modules ship their own structured errors.

## Findings

- Three structured error classes exist, all with identical method signatures and field contracts.
- No common base beyond `ValueError`.
- Architecture review identified this as an expensive-to-reverse decision.
- Batch 4+ will add more (outreach drafting, scoring calibration).

## Proposed Solutions

### Option 1: Add `StructuredError` base in `utils.py`, retrofit existing classes

**Approach:**
```python
# utils.py
class StructuredError(ValueError):
    """Base for all structured, agent-consumable errors in job-hunt."""
    ALLOWED_ERROR_CODES: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, message: str, error_code: str, url: str = "", remediation: str = ""):
        super().__init__(message)
        assert error_code in self.ALLOWED_ERROR_CODES, f"unknown: {error_code}"
        self.error_code = error_code
        self.url = url
        self.remediation = remediation

    def to_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "url": self.url,
            "remediation": self.remediation,
        }


class IngestionError(StructuredError):
    ALLOWED_ERROR_CODES = INGESTION_ERROR_CODES


class PdfExportError(StructuredError):
    ALLOWED_ERROR_CODES = PDF_EXPORT_ERROR_CODES


class DiscoveryError(StructuredError):
    ALLOWED_ERROR_CODES = DISCOVERY_ERROR_CODES
```

CLI error handlers become one `except StructuredError as exc: print(json.dumps(exc.to_dict()))`.

**Pros:** 15 LOC. Eliminates N catch-handlers for N error types. Future-proofs batch 4+.
**Cons:** Touches batch 1 and batch 2 code (small blast radius — three classes).
**Effort:** Small (1-2 hours).
**Risk:** Low.

### Option 2: Defer — leave three parallel hierarchies

**Approach:** Ship batch 3 with DiscoveryError subclassing ValueError directly; revisit when batch 4 adds a fourth.

**Pros:** No batch-2 retrofit.
**Cons:** Debt compounds. Each new module adds a new handler branch.
**Risk:** Medium (grows painful fast).

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Phase 1 Deliverables
- `src/job_hunt/utils.py` — add `StructuredError`
- `src/job_hunt/ingestion.py` — retrofit `IngestionError`
- `src/job_hunt/pdf_export.py` — retrofit `PdfExportError`
- Phase 3 new: `src/job_hunt/discovery.py` — `DiscoveryError(StructuredError)`

## Acceptance Criteria

- [ ] `StructuredError` defined in `utils.py`.
- [ ] Existing `IngestionError`, `PdfExportError` inherit from it.
- [ ] `DiscoveryError` inherits from it.
- [ ] `core.py` CLI error handlers catch `StructuredError` uniformly.
- [ ] All 156 batch-2 tests continue to pass (inheritance is additive).
- [ ] New test: `test_structured_error_common_interface` — all three subclasses expose identical API.

## Work Log

### 2026-04-16 - Discovered during post-deepen architecture review

**By:** architecture-strategist

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- `src/job_hunt/ingestion.py` `IngestionError` definition
- `src/job_hunt/pdf_export.py` `PdfExportError`
