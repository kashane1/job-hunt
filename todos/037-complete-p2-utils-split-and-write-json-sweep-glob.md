---
status: pending
priority: p2
issue_id: "037"
tags: [code-review, architecture, batch-3, modularity]
dependencies: []
---

# Split utils.py or keep; drop `_fetch = fetch` alias; verify write_json sweep glob

## Problem Statement

Three related architectural cleanups:

1. **`utils.py` bloat:** Plan adds ~250 LOC (`DomainRateLimiter` + `RobotsCache`) to a 107-LOC module. `RobotsCache` owns persistent state and is not a pure utility.
2. **`_fetch = fetch` backward-compat alias:** All callers are in-repo. No external consumer. Alias accrues dual naming and deprecation debt.
3. **`write_json` tempfile glob:** Upgrade to `tempfile.mkstemp` means stale tmp files use random names, not `*.tmp`. Plan's "startup sweep for stale `.tmp` files" must explicitly handle the new naming or fail silently.

## Findings

- Architecture review: "utils.py scope creep is a legitimate concern."
- Architecture review: "`_fetch = fetch` alias should be removed in-repo."
- Architecture review (new risk): "verify sweep glob catches mkstemp names, or switch to stable-prefixed naming."

## Proposed Solutions

### Option 1: Separate net_policy module + no alias + prefixed tempfile

**Approach:**
1. Move `DomainRateLimiter`, `RobotsCache` (+ `registered_domain`, `KNOWN_SHARED_DOMAINS`) to `src/job_hunt/net_policy.py` (or `http_courtesy.py`). `utils.py` stays primitives-only.
2. Rename `_fetch` → `fetch` in `ingestion.py` and update call sites in the same PR. Drop alias.
3. In `write_json`, use `tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)` — tempfile names are discoverable via `<path.name>.*.tmp` OR just `*.tmp` suffix pattern. Sweep glob at startup uses `*.tmp` (caught both batch-2 `.tmp` names and new prefixed mkstemp names).

**Pros:** Clear separation. Single public name. Sweep is deterministic.
**Cons:** Cross-file rename adds small PR scope.
**Effort:** Small-medium.
**Risk:** Low.

### Option 2: Keep `utils.py` monolithic; remove alias; leave tempfile glob risk

**Approach:** Only tackle (2). Defer (1) and (3) to batch 4.

**Pros:** Minimal diff.
**Cons:** `utils.py` continues to accrete. Silent sweep gap persists.
**Effort:** Minimal.
**Risk:** Medium.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Module structure, §Modified files, §Phase 1 Deliverables
- `src/job_hunt/utils.py` (splits content OR retains)
- `src/job_hunt/net_policy.py` (new, if Option 1)
- `src/job_hunt/ingestion.py` (`_fetch` → `fetch`)

## Acceptance Criteria

- [ ] Plan specifies module split OR justifies keeping monolithic.
- [ ] Plan removes `_fetch = fetch` alias from Phase 1 Deliverables.
- [ ] `write_json` tempfile pattern is documented; startup sweep glob is defined.
- [ ] Test: `test_startup_sweep_catches_mkstemp_stragglers`.

## Work Log

### 2026-04-16 - Architecture review #1, #2, new-risk

**By:** architecture-strategist

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- `src/job_hunt/utils.py` (current 107 LOC)
- `src/job_hunt/ingestion.py`
