---
title: "Flat-lookup on a nested return value silently forced every draft to tier_2"
category: logic-errors
tags: [defensive-defaults, two-phase-write, dict-shape-mismatch, ats, python]
module: job_hunt.application
symptom: "Every prepared draft reports tier_2 with rationale ats_status:not_checked, even though the ATS check ran and wrote real errors to disk"
root_cause: "_run_ats_check flat-read record.get('status') on a helper that returns a content record whose status lives at record['ats_check']['status']; the .get default 'not_checked' was itself a valid enum member, so the miss never surfaced"
severity: high
date: 2026-04-18
---

# Flat-lookup on a nested return value silently forced every draft to tier_2

## Problem

Running `apply-batch --top 10 --source indeed` returned 10/10 drafts at
`tier: "tier_2"` with `tier_rationale: "ats_status:not_checked"`, even
though the resume template produced short keyword-stuffed output that
should have tripped `ats_status: errors`. Tier_2 means the agent pauses
on every field before filling, so the whole batch was effectively
blocked behind a non-existent failure reason.

The ATS check itself was working. The report file at
`data/generated/ats-check/{content_id}-check.json` contained the real
errors ("Resume has 196 words; minimum 200", "Keyword density 17.2%
exceeds 5%"). Only the in-memory path through
`src/job_hunt/application.py:_run_ats_check` was lying about them.

Root cause: `_run_ats_check` called
`run_ats_check_with_recovery(record_path, lead, ats_dir)` from
`src/job_hunt/ats_check.py` and treated the return value as if it were
the ATS report. It isn't. `run_ats_check_with_recovery` does a two-phase
write on a **content record dict** and returns the full record. The ATS
result lives nested at `record["ats_check"] = {"status", "report_path",
"checked_at"}`. The buggy code read `ats_report.get("status",
"not_checked")` at the top level, where no such key exists.

## Why the failure was silent

Three things had to line up for the bug to go undetected:

1. **The default value was a valid enum member.** `.get("status",
   "not_checked")` returned the string `"not_checked"`, which is a
   legitimate `ats_check.status` value elsewhere in the schema. No
   `KeyError`, no type error, no "unexpected enum" assertion anywhere
   downstream.
2. **`_compute_tier` treated `not_checked` as tier_2 rather than
   raising.** The non-passing-status list swallowed the sentinel and
   produced a superficially plausible rationale.
3. **The report file on disk was correct.** The two-phase write had
   already serialized the right errors/warnings to
   `data/generated/ats-check/*.json`, so spot-checking the artifacts
   made everything look fine. Only the return-path read was broken, and
   the return path had no artifact to inspect.

Net effect: a wrong default masked a wrong lookup, and the batch tier
report confidently misreported the failure mode for every single lead.

## Solution

Two changes in `_run_ats_check`
(`src/job_hunt/application.py`, commit `cefb74d`):

1. **Read `status` from the right place.** `record["ats_check"]["status"]`,
   not `record["status"]`.
2. **Load errors/warnings from `report_path`.** They aren't on the
   record at all; the helper only puts `status`, `report_path`, and
   `checked_at` on `record["ats_check"]`. The real error/warning lists
   live in the report JSON written by `run_ats_check` itself.

```python
record = run_ats_check_with_recovery(record_path, lead, ats_dir)
ats_meta = record.get("ats_check") or {}
status = str(ats_meta.get("status") or "not_checked")
errors: list[str] = []
warnings: list[str] = []
report_path = ats_meta.get("report_path")
if report_path:
    try:
        report = read_json(Path(report_path))
        errors = [str(e) for e in report.get("errors", [])]
        warnings = [str(w) for w in report.get("warnings", [])]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("ATS report unreadable at %s: %s", report_path, exc)
if status == "check_failed" and ats_meta.get("error"):
    errors.append(str(ats_meta["error"]))
return status, errors, warnings
```

The `check_failed` branch is handled explicitly because in that case
`run_ats_check_with_recovery` never writes a report file: it stores the
exception message inline at `record["ats_check"]["error"]`, and we want
that string surfaced instead of lost.

### Verification

Re-running the same Veeva lead under the fix produced `status:
"errors"` with the real messages ("Resume has 196 words; minimum 200",
"Keyword density 17.2% exceeds 5%"). Tier breakdown for the batch
flipped from 10/10 tier_2 `ats_status:not_checked` to 6 tier_2
`ats_status:errors` + 4 tier_2 `ats_status:warnings`, which matches
what the on-disk reports actually said.

## Prevention

General rules that would have caught this or kept it loud:

1. **At an API boundary, know the shape of what you get back.** If the
   helper returns a *record* (the whole thing it was mutating), don't
   treat it as if it returned the *result* (the piece it just
   computed). A one-line comment at the call site naming the shape
   ("returns content record, not report") is cheap insurance. The fix
   now carries that comment.
2. **Never default to a valid enum member on a required field.** If the
   key is load-bearing, let the miss blow up. Use `record["ats_check"]["status"]`
   and raise on `KeyError`, or default to a sentinel (`"__missing__"`,
   `None`) that downstream code will refuse. Defaults that happen to be
   in the domain's value set turn bugs into silent data corruption.
3. **Flat `.get("status")` on a nested dict is almost always wrong.**
   If the producer writes `record["ats_check"] = {...}`, the consumer
   must either read `record["ats_check"]["status"]` or assert that the
   shape was flattened somewhere in between. Don't guess.
4. **Two-phase-write helpers usually return the record, not the
   result.** That's the point of the pattern: the caller wants the
   post-patch state of the thing it was persisting. When you see
   `pending → result` or `optimistic → confirmed`, expect the return
   value to be the container, and look for the result at a nested key.

## Related

- `src/job_hunt/application.py:_run_ats_check`. the fixed reader.
- `src/job_hunt/ats_check.py:run_ats_check_with_recovery`. the
  two-phase-write helper; docstring now makes the return shape
  explicit.
- Commit `cefb74d fix(discovery-hardening): Phase 6 — advisory
  warnings, report-path load, tier back-fill`.
