---
status: pending
priority: p2
issue_id: "021"
tags: [code-review, data-integrity, language-precision, batch-2]
dependencies: []
---

# "Atomic two-phase ATS write" is misleading — it's crash-safe but not atomic; extract to helper

## Problem Statement

The plan calls the two-phase ATS write "atomic" (Enhancement Summary #2, deliverable at line 1133). Python reviewer flagged that `write_json` is atomic per-call (tempfile + os.replace), but the **sequence** of write-pending → run-check → write-status is NOT atomic. A SIGKILL between the two writes leaves the record in `pending` indefinitely — self-healing via `check-integrity`, but not atomic.

Secondary issue: the two-phase orchestration is inline in `core.py::main()`'s CLI dispatch. Python reviewer recommends extracting to a helper to keep `main()` readable.

## Findings

### Misleading language

Plan language:
- "Atomic two-phase update" (Enhancement Summary item 2)
- "ATS check is now atomic" (deliverable)

Actual behavior:
- Phase 1: `write_json({..., ats_check: pending})` — atomic per-call ✅
- Phase 2: Run check (maybe long — token counting, file reads) — crash window ⚠️
- Phase 3: `write_json({..., ats_check: status})` — atomic per-call ✅

A crash between Phase 1 and Phase 3 leaves the record as `pending` permanently, waiting for `check-integrity` to surface it and the user to re-run `ats-check`.

### Coupling issue

The orchestration is ~30 lines inline in `core.py::main()`:
```python
if args.command == "generate-resume":
    records = generate_resume_variants(...)
    if not args.skip_ats_check:
        for record in records:
            content_record_path = output_dir / f"{record['content_id']}.json"
            current = read_json(content_record_path)
            current["ats_check"] = {"status": "pending", "checked_at": now_iso()}
            write_json(content_record_path, current)
            try:
                report = run_ats_check(current, lead, ats_check_dir)
                current["ats_check"] = {...}
            except Exception as exc:
                current["ats_check"] = {"status": "check_failed", "error": str(exc)}
            write_json(content_record_path, current)
```

`main()` already 100+ lines; this inflates it. Extract.

### Other Python-review subtleties

- `current["ats_check"]["checked_at"]` is set twice (at pending-write time and at result-write time). Pick one timestamp source or clearly document both.
- `read_json(content_record_path)` is redundant — `record` is already in scope from the loop variable.
- On the exception path, `current` is mutated twice; if a second process reads the file between Phase 1 and Phase 3, they see `pending`. Probably fine for single-user but document.

## Proposed Solutions

### Option 1: Fix language + extract helper + clean up timestamps (Recommended)

**Rename to "crash-safe two-phase update with recovery via check-integrity"** in Enhancement Summary and deliverables. Remove "atomic" where the sequence is not atomic. Keep "atomic" where it does apply (per-call `write_json` via `os.replace`).

**Extract to helper in `ats_check.py` or `generation.py`:**
```python
def run_ats_check_with_recovery(
    record_path: Path,
    lead: dict | None,
    ats_check_dir: Path,
) -> dict:
    """Crash-safe two-phase update:
    1. Mark ats_check.status=pending on the record (atomic write).
    2. Run the check (may raise).
    3. Patch record with result or check_failed (atomic write).

    A crash between 1 and 3 leaves the record in 'pending' — `check-integrity`
    surfaces these for re-run. Not atomic at the sequence level — crash-safe.
    """
    record = read_json(record_path)
    record["ats_check"] = {"status": "pending", "checked_at": now_iso()}
    write_json(record_path, record)

    try:
        report = run_ats_check(record, lead, ats_check_dir)
        record["ats_check"] = {
            "status": report["status"],
            "report_path": str(ats_check_dir / f"{report['report_id']}.json"),
            "checked_at": report["checked_at"],
        }
    except Exception as exc:
        record["ats_check"] = {
            "status": "check_failed",
            "error": str(exc),
            "checked_at": now_iso(),
        }
    write_json(record_path, record)
    return record
```

CLI dispatch becomes:
```python
if args.command == "generate-resume":
    records = generate_resume_variants(...)
    if not args.skip_ats_check:
        for record in records:
            path = output_dir / f"{record['content_id']}.json"
            run_ats_check_with_recovery(path, lead, ats_check_dir)
```

**Effort:** Small
**Risk:** Low

## Recommended Action

Option 1. Fixes the misleading "atomic" language, keeps `main()` readable, and documents the actual behavior.

## Acceptance Criteria

- [ ] "Atomic two-phase update" renamed to "crash-safe two-phase update" in Enhancement Summary and deliverables
- [ ] `run_ats_check_with_recovery` (or similar) extracted to `ats_check.py` or helper module
- [ ] CLI dispatch in `core.py::main()` is <5 lines per command
- [ ] Test: simulate crash between phase 1 and phase 3 → record is `pending`, `check-integrity` surfaces it
- [ ] Test: `ats-check --content-id <id>` can re-run on a `pending` record

## Work Log

### 2026-04-16 - Discovery

**By:** kieran-python-reviewer

**Actions:**
- Identified "atomic" as misleading for a multi-write sequence
- Recommended extracting the orchestration to a helper
