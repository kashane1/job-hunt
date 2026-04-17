---
status: pending
priority: p3
issue_id: "043"
tags: [code-review, batch-3, style, nits]
dependencies: []
---

# Naming, typing, and documentation nits roll-up

## Problem Statement

Collection of small stylistic/correctness items flagged by reviewers, none individually blocking. Rolled up to avoid 7 tiny todos.

## Findings

1. **`ListingEntry` is `frozen=True` with `list[str]` default.** Frozen prevents re-assignment but not in-place mutation. Use `tuple[str, ...]` for true immutability OR document the "frozen means rebinding only" semantics.

2. **`SourceRun` and `DiscoveryResult` are not frozen; `ListingEntry`/`Outcome` are.** Add a one-line comment justifying the asymmetry (built incrementally vs. final).

3. **`Confidence = Literal["high", "weak_inference"]`** — asymmetric naming ("high" is adjective, "weak_inference" is noun phrase). Rename to `Literal["high", "low"]` OR `Literal["confident", "weak_inference"]`.

4. **`DISCOVERY_USER_AGENT = "job-hunt/0.3 (+https://github.com/local/job-hunt)"`** — the contact URL doesn't resolve. A non-contactable "contact URL" in the UA defeats the polite-crawler convention. Either drop `+URL` or use a real placeholder documented as "user should update."

5. **`registered_domain`** imported into `discovery.py` but may not be used directly at module scope (only via `DomainRateLimiter`). Verify import is necessary; remove if not.

6. **Error code `review_schema_invalid`** is in `DISCOVERY_ERROR_CODES` but no specific raise site shown in plan. Either add a raise site OR remove.

7. **`_append_discovered_via` name uses underscore** (internal) but plan's prose calls it a "merge helper" — clarity drift. Either make it public (`append_discovered_via`) or rename prose to "internal merge helper."

## Proposed Solutions

### Option 1: Fix all 7 in one pass

**Approach:** Small plan/code edits for each item. Collectively ~15 line changes.

**Pros:** Clean-up done.
**Cons:** Minor churn.
**Effort:** Small.
**Risk:** Low.

### Option 2: Defer all to batch 4 code cleanup

**Approach:** Ship v2 as-is; address in a dedicated polish PR.

**Pros:** Keeps batch 3 focused.
**Cons:** Debt accumulates.
**Risk:** Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Module structure, §Constants

## Acceptance Criteria

- [ ] `ListingEntry.signals` uses `tuple[str, ...]` OR docs the frozen semantics.
- [ ] Frozen/non-frozen asymmetry commented.
- [ ] `Confidence` enum values symmetric.
- [ ] `DISCOVERY_USER_AGENT` contact URL resolves OR contact fragment dropped.
- [ ] `registered_domain` import verified necessary.
- [ ] `review_schema_invalid` raise site specified OR removed.
- [ ] `_append_discovered_via` internal-vs-public prose is consistent.

## Work Log

### 2026-04-16 - Post-deepen nits

**By:** kieran-python-reviewer, security-sentinel

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
