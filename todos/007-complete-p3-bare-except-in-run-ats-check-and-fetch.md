---
status: pending
priority: p3
issue_id: 007
tags: [code-review, error-handling]
dependencies: []
---

# Narrow bare `except Exception` in two new spots

## Problem Statement

Two bare `except Exception:` clauses were introduced in Phase 6 / Phase 2 that silently swallow all errors. Narrow-or-log so silent corruption stays visible.

## Findings

- `src/job_hunt/application.py:693-697` — `try: report = read_json(Path(report_path)) except Exception: pass` — if the report is corrupted, _run_ats_check returns empty errors/warnings silently.
- `src/job_hunt/ingestion.py:564-567` — `except Exception: raw_retry = ""` around `exc.headers.get("Retry-After", "")`. `Headers.get` is hard to make raise; narrow to `AttributeError`.

## Proposed Solutions

**A. Narrow + add a logger.debug in the report-load path.** Keeps the flow tolerant but not invisible.

## Recommended Action

Option A.

## Acceptance Criteria

- [ ] `_run_ats_check`'s `except Exception` becomes `except (OSError, ValueError, json.JSONDecodeError) as exc: logger.debug("ATS report unreadable: %s", exc)`.
- [ ] `ingestion.fetch`'s retry-header `except Exception` becomes `except AttributeError:` (the only case that actually occurs when `exc.headers` is None).
- [ ] Tests still pass.

## Resources

- Review: kieran-python-reviewer findings on PR #3
