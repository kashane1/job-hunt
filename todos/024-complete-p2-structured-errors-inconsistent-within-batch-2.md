---
status: pending
priority: p2
issue_id: "024"
tags: [code-review, pattern, errors, batch-2]
dependencies: []
---

# Structured error pattern inconsistent within batch 2; base class should be ValueError

## Problem Statement

Pattern review flagged:

1. The plan introduces `PdfExportError` and `IngestionError` with structured `error_code` + `to_dict()` — a new convention vs batch 1's use of plain `ValueError` everywhere (only custom exception is `ValidationError(ValueError)` in `schema_checks.py`).

2. This new convention is applied to `ingestion.py` and `pdf_export.py` but **NOT** to `ats_check.py` and `analytics.py` — inconsistent within batch 2 itself.

3. Both new exception classes inherit bare `Exception` rather than `ValueError`, breaking batch 1's ancestor pattern.

## Findings

### Convention divergence

Batch 1 pattern:
- `ValueError` raised directly with a message string
- `ValidationError(ValueError)` is the only custom exception class
- No structured error codes anywhere

Batch 2 plan:
- `PdfExportError(Exception)` with `error_code`, `remediation`, `to_dict()`
- `IngestionError(Exception)` with same shape
- No custom exceptions for `ats_check.py` or `analytics.py` — they would presumably raise `ValueError`

### Why the pattern change is justifiable

The structured errors serve agent consumption (from todo 018's CLI contract). Raw `ValueError` messages require string-matching by consumers. For agent-facing modules (I/O boundaries where external data enters the system), structured errors are a real improvement.

### But inconsistency creates confusion

A developer looking at the codebase sees:
- `ats_check.py` uses plain `ValueError`
- `ingestion.py` uses `IngestionError` with `error_code`
- No rule explaining when to use which

## Proposed Solutions

### Option 1: Structured errors ONLY for I/O-boundary modules, document the rule (Recommended)

**Rule:** Modules with external or user-controllable inputs use structured errors. Internal logic uses plain `ValueError`.

- `ingestion.py` — external: yes, use `IngestionError`
- `pdf_export.py` — external (weasyprint native): yes, use `PdfExportError`
- `ats_check.py` — internal logic: no, use `ValueError`
- `analytics.py` — internal logic: no, use `ValueError`
- `tracking.py`, `research.py`, `reminders.py`, `generation.py` — stay with batch 1 style

**Also fix:** base class to `ValueError`:
```python
class IngestionError(ValueError):
    def __init__(self, message: str, error_code: str, url: str = "", remediation: str = ""):
        super().__init__(message)
        ...
```

Matches `ValidationError(ValueError)` ancestor and allows callers to `except ValueError` generically.

**Document the rule** in Enhancement Summary or AGENTS.md:
> Structured error codes (`error_code`, `remediation`, `to_dict()`) are used for modules that ingest external data or surface to the CLI/agent boundary. Internal logic modules raise `ValueError` directly.

**Effort:** Small (plan edits + inheritance change)
**Risk:** Low

### Option 2: Extend pattern to all four modules (Completeness-first)

Add `AtsCheckError(ValueError)` and `AnalyticsError(ValueError)` with same shape. But `ats_check.py`'s errors are mostly internal bugs (invalid content_id, missing fields) — structured codes don't add value. Probably over-engineering.

## Recommended Action

Option 1. Document the rule, fix the base class, leave the pattern where it earns its keep.

## Acceptance Criteria

- [ ] `IngestionError` and `PdfExportError` inherit from `ValueError`
- [ ] Rule documented: "structured errors for I/O boundary modules, plain `ValueError` elsewhere"
- [ ] Rule added to Enhancement Summary or a convention doc
- [ ] `ats_check.py` and `analytics.py` explicitly use `ValueError` (no custom exception classes introduced)

## Work Log

### 2026-04-16 - Discovery

**By:** pattern-recognition-specialist

**Actions:**
- Traced exception classes across batch 1 and batch 2
- Only `ValidationError(ValueError)` exists in batch 1
- New classes inherit bare `Exception`, breaking the ancestor convention
