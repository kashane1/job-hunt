---
status: pending
priority: p2
issue_id: "020"
tags: [code-review, python, types, batch-2]
dependencies: []
---

# AggregatedRow TypedDict promised but not shown in code; return types still loose

## Problem Statement

Python review and architecture review both flagged: deliverables promise `AggregatedRow` TypedDict to lock the shape shared across three analytics reports, but the code examples in the plan still show `list[dict]` return types everywhere. Three consumers (`report_dashboard`, `report_skills_gap`, `report_rejection_patterns`) each index `a["lead_id"]`, `a["fit_score"]`, `a["current_stage"]`, `a["missing_skills"]` without a shared type to pin the schema.

Same issue for the three error `to_dict()` return types, the fetched-posting dict in ingestion, and the `_fetch_greenhouse` / `_fetch_lever` shapes.

## Findings

### Promised but not delivered

Plan deliverable (Phase 4): "`AggregatedRow` `TypedDict` locks the aggregator output shape (prevents divergence across 3 consumer reports)."

Plan code still shows:
```python
def build_aggregator(data_root: Path) -> list[dict]: ...
def report_dashboard(...) -> dict: ...
def report_skills_gap(...) -> dict: ...
def report_rejection_patterns(...) -> dict: ...
```

### Related type-safety gaps (Python reviewer)

1. `IngestionError.to_dict()` returns `dict` — should be `dict[str, str]` or a TypedDict
2. `PdfExportError.to_dict()` same
3. `_fetch_greenhouse` / `_fetch_lever` / `_fetch_generic_html` all return `dict` with the same 5-6 keys — candidates for `FetchedPosting` TypedDict
4. `_safe_url_fetcher(url: str)` return type missing
5. `_weasyprint_or_raise()` return type missing
6. `_StrictRedirectHandler.redirect_request` parameters untyped

## Proposed Solutions

### Option 1: Add TypedDicts where they matter, keep batch-1 style elsewhere (Recommended)

**`AggregatedRow` TypedDict** (analytics.py):
```python
from typing import TypedDict, NotRequired

class AggregatedRow(TypedDict):
    lead_id: str
    current_stage: str
    transitions: list[dict]
    applied_date: NotRequired[str | None]
    lead_title: str
    lead_company: str
    fit_score: NotRequired[float | None]
    matched_skills: NotRequired[list[str]]
    missing_skills: NotRequired[list[str]]
    company_stage: NotRequired[str | None]
    company_industry: NotRequired[str | None]
    company_remote_policy: NotRequired[str | None]
    company_fit_score: NotRequired[float | None]
    selected_variant_style: NotRequired[str | None]
    generated_content_ids: NotRequired[list[str]]

def build_aggregator(data_root: Path) -> list[AggregatedRow]: ...
```

**`FetchedPosting` TypedDict** (ingestion.py):
```python
class FetchedPosting(TypedDict):
    title: str
    company: str
    location: str
    raw_description_html: str
    source: str
    ingestion_method: str
    compensation: NotRequired[str]
    ingestion_notes: NotRequired[str]

def _fetch_greenhouse(company: str, job_id: str) -> FetchedPosting: ...
```

**Error `to_dict` typing:**
```python
def to_dict(self) -> dict[str, str]: ...
```

**Type missing annotations:**
```python
def _safe_url_fetcher(url: str) -> dict: ...  # WeasyPrint fetcher contract
def _weasyprint_or_raise() -> "types.ModuleType": ...
def redirect_request(
    self,
    req: urllib.request.Request,
    fp,  # socket stream — leave untyped, matches stdlib
    code: int,
    msg: str,
    headers: "HTTPMessage",
    newurl: str,
) -> urllib.request.Request | None: ...
```

**Effort:** Small (plan edits, maybe ~30 lines of TypedDict + signatures)
**Risk:** Low

## Recommended Action

Option 1. TypedDicts for the 2-3 shapes with multiple consumers; simple dict return types for 1-off helpers. Don't over-do it — match batch 1's pragmatic style elsewhere.

## Acceptance Criteria

- [ ] `AggregatedRow` TypedDict defined in `analytics.py`, used as return type of `build_aggregator`
- [ ] All three report functions document what fields they read from `AggregatedRow`
- [ ] `FetchedPosting` TypedDict defined in `ingestion.py`, used by all three `_fetch_*` helpers
- [ ] `IngestionError.to_dict() -> dict[str, str]` and same for `PdfExportError`
- [ ] `_safe_url_fetcher`, `_weasyprint_or_raise` have return type annotations
- [ ] `mypy` or equivalent type check passes (add if not already in CI)

## Work Log

### 2026-04-16 - Discovery

**By:** kieran-python-reviewer, architecture-strategist

**Actions:**
- Promised `AggregatedRow` in deliverables but never shown in code blocks
- Three separate consumers indexing `a["..."]` without a shared type contract
