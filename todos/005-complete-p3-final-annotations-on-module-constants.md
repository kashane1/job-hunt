---
status: pending
priority: p3
issue_id: 005
tags: [code-review, python-style, nits]
dependencies: []
---

# Add `Final` annotations to new module-scope constants

## Problem Statement

Three new module-scope constants ship without `Final` annotations, inconsistent with the rest of the repo's convention (`DISCOVERY_USER_AGENT`, `INGESTION_ERROR_CODES`, `STALE_COMPANY_DENYLIST` all use `Final`).

## Findings

- `src/job_hunt/core.py` — `KEYWORD_STOPWORDS: frozenset[str] = frozenset({...})` → should be `KEYWORD_STOPWORDS: Final[frozenset[str]] = frozenset({...})`
- `src/job_hunt/generation.py:40` — `CURATED_RESUME_LANES: list[tuple[tuple[str, ...], str]] = [...]` → should be `CURATED_RESUME_LANES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (...)` (also promote to a tuple since the list isn't mutated)

## Proposed Solutions

**A. Add `Final` annotations + convert `CURATED_RESUME_LANES` to a tuple.**

## Recommended Action

Option A.

## Acceptance Criteria

- [ ] `KEYWORD_STOPWORDS` carries `Final[frozenset[str]]`.
- [ ] `CURATED_RESUME_LANES` is a `Final[tuple[...]]` with tuple literal.
- [ ] Tests still pass.

## Resources

- Review: kieran-python-reviewer findings on PR #3
