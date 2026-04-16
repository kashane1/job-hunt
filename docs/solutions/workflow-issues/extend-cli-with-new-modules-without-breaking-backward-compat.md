---
title: Extend a growing single-file CLI with new modules without breaking backward compatibility
date: 2026-04-16
module: core_architecture
problem_type: workflow_issue
component: src/job_hunt/
symptoms:
  - core.py grew to 1800+ lines with utility functions tangled alongside domain logic
  - Adding new domain modules (tracking, generation, research, reminders) risked bidirectional imports since both new modules and core.py needed the same helpers
  - Adding new schema fields to existing artifacts (lead, application-draft) risked breaking validation on historical files
  - PII in candidate profiles (name, email, phone, salary, work authorization) could accidentally be pushed to remote
  - Paired .json + .md content writes could corrupt on interrupted runs (e.g. SIGKILL between writes)
root_cause: Single-file codebases that grow past ~1500 lines conflate pure utilities with domain logic. When new modules are added, the obvious import graph is bidirectional (new module imports helpers from core; core wants to use the new module), which cannot be resolved cleanly in Python. Additionally, schema evolution without explicit backward-compatibility rules will break validation on existing artifacts the first time a field is added to a `required` array.
tags:
  - python
  - cli
  - architecture
  - schema-evolution
  - backward-compatibility
  - file-backed
  - pii
---

# Extend a growing single-file CLI with new modules without breaking backward compatibility

## Context

The job-hunt repository had a single 1800-line `src/job_hunt/core.py` containing everything: CLI dispatch, domain logic for profile normalization and lead scoring, and 15+ pure utility functions (`write_json`, `slugify`, `short_hash`, `tokens`, etc.).

A plan called for adding five new domain modules (tracking, generation, research, reminders) that all needed the same utility functions. Naively importing them from `core.py` would create a cycle the moment `core.py` wanted to dispatch to the new modules.

## The Problems

### 1. Bidirectional import risk
New modules like `tracking.py` need `write_json`, `read_json`, `slugify`, etc. — all currently in `core.py`. `core.py`'s CLI dispatcher also needs to call functions from `tracking.py`. Result: `from .core import write_json` in `tracking.py` combined with `from .tracking import update_application_status` in `core.py` creates a cycle.

### 2. Schema evolution breaking existing artifacts
Adding fields like `selected_resume_content_id` to `application-draft.schema.json` or `company_research_id` to `lead.schema.json` would retroactively fail validation on every existing artifact unless handled carefully.

### 3. PII exposure
Generated resumes, cover letters, and answer sets intentionally contain PII (name, email, phone, salary expectations, work authorization status). A single `git push` could expose everything.

### 4. Interrupted writes corrupting state
`Path.write_text(...)` is not atomic. A SIGKILL or power failure mid-write leaves a partial file. For paired artifacts (`foo.json` + `foo.md` that must be consistent), a crash between the two writes leaves them out of sync.

## The Solution

### 1. Extract pure utilities to `utils.py` with re-exports

Create `src/job_hunt/utils.py` containing only pure, domain-free helpers:

```python
# src/job_hunt/utils.py
from .simple_yaml import loads as load_yaml

def write_json(path: Path, payload: dict) -> None: ...
def read_json(path: Path) -> dict: ...
def slugify(text: str) -> str: ...
# ... etc
```

In `core.py`, re-export everything for backward compatibility:

```python
# src/job_hunt/core.py
from .utils import (  # noqa: F401
    display_path, ensure_dir, load_yaml_file, meaningful_lines,
    now_iso, parse_frontmatter, read_json, repo_root, short_hash,
    slugify, tokens, unique_preserve_order, write_json,
)
```

New modules import directly from `utils.py`:

```python
# src/job_hunt/tracking.py
from .utils import ensure_dir, now_iso, read_json, write_json
```

Result: one-directional imports everywhere. `core.py → utils`, `tracking → utils`, `core → tracking` via lazy imports inside CLI handlers. No cycles.

### 2. Backward-compatible schema extensions

**Rule: new fields on existing schemas are NEVER added to the `required` array.** They are added as optional properties only.

```json
// schemas/lead.schema.json
{
  "required": ["lead_id", "fingerprint", ...],
  "properties": {
    ...existing fields,
    "company_research_id": { "type": "string" },      // NEW — optional
    "application_status_path": { "type": "string" }   // NEW — optional
  }
}
```

Code accessing new fields must use `.get()` with sensible defaults:

```python
crid = lead.get("company_research_id")  # works on old artifacts
```

### 3. PII protection via directory-level `.gitignore`

Generated content intentionally contains PII — field-level redaction is wrong. Use directory-level exclusion instead:

```
# .gitignore
profile/raw/
profile/normalized/
data/generated/
data/companies/
data/applications/
data/leads/
data/runs/
docs/reports/*-report.md
examples/results/
*.tmp
```

Keep `SENSITIVE_KEYWORDS` narrowly scoped — it protects against credential leakage in browser attempt payloads via `redact_sensitive_data()`, NOT PII in generated content. Do NOT add `"email"`, `"phone"`, or `"authorization"` — those are legitimate keys in the candidate profile.

```python
SENSITIVE_KEYWORDS = (
    "password", "passwd", "secret", "token", "otp",
    "one_time_code", "verification_code", "session", "cookie",
    "salary", "compensation",  # credentials-adjacent, not PII-at-large
)
```

### 4. Atomic `write_json` with `os.replace()`

```python
import os

def write_json(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
```

Use `os.replace()` instead of `Path.rename()` — `os.replace()` is atomic on all platforms including Windows where `rename()` fails if the destination exists.

### 5. Lazy imports in CLI dispatcher

To keep `core.py` from pulling in every module at import time, dispatch to new modules via function-scoped imports:

```python
# core.py main()
if args.command == "update-status":
    from .tracking import create_application_status, update_application_status
    ...

if args.command == "generate-resume":
    from .generation import generate_resume_variants
    ...
```

This keeps `core.py` startup fast and makes the module boundaries explicit.

## Verification

- All 6 pre-existing tests continue to pass unchanged (no regressions in existing callers of `write_json`, `read_json`, etc.)
- 44 new tests across 4 new test files all pass
- Schema validation passes for all existing artifacts with the extended schemas
- Paired `.json` + `.md` writes now survive interruption (tmp file cleaned up on exception)

## Prevention

### Before adding to a growing single-file module

Ask: "Will new code need the same helpers that existing code uses?" If yes, extract the helpers BEFORE adding the new code. Doing it after creates a two-commit migration (extract, then use); doing it before creates a one-commit migration with no circular-import risk.

### Before extending a JSON schema

Ask: "Will this break validation on files already on disk?" Check:
1. Is the field being added to `required`? → NO unless there's a migration script
2. Are existing `required` fields being removed? → NO without a deprecation cycle
3. Are existing field types being narrowed (e.g., string → enum)? → NO without backfilling

### Before writing PII to disk

Ask: "Is this directory in `.gitignore`?" If the answer is "I'm not sure", add it. Directory-level exclusion is cheaper to get right than per-file redaction and doesn't require downstream code to remember to call a redactor.

### Before a multi-file write operation

If two files must be consistent (e.g., `foo.json` metadata + `foo.md` content), write both to `.tmp` suffixes first, then atomically rename both. If one tmp write fails, unlink both tmp files and raise — the observer never sees inconsistent state.

## Related

- [`docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md`](../workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md) — The plan-review that produced the simplification decisions this implementation followed
- [`docs/solutions/security-issues/`](../security-issues/) — PII handling patterns
- Plan: [`docs/plans/2026-04-15-002-feat-content-generation-and-tracking-plan.md`](../../plans/2026-04-15-002-feat-content-generation-and-tracking-plan.md)
- Commits on `feat/content-generation-and-tracking` branch:
  - `a0f9c4e` refactor: extract utils.py, atomic write_json, expand SENSITIVE_KEYWORDS
  - `8f4c7e4` feat(tracking): add application status tracker, integrity checks, and schemas
  - `f98232e` feat(generation): add resume variants, answer matching, and cover letter generation
  - `d524c9d` feat(research,reminders): add company research, fit scoring, and follow-up system
