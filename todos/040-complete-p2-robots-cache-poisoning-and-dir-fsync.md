---
status: pending
priority: p2
issue_id: "040"
tags: [code-review, security, data-integrity, batch-3]
dependencies: []
---

# robots_cache.json poison persistence + parent-directory fsync + `_LEAD_WRITE_LOCKS` leak

## Problem Statement

Three smaller-blast but real durability/integrity gaps bundled together:

1. **robots_cache.json poison persistence.** One run gets a compromised DNS answer → caches "allowed" for 24h → subsequent legit runs honor the poisoned decision. No manual clear path; user must `rm` the file.
2. **`write_json` upgrade fsyncs the file but not the parent directory.** On Linux ext4 with `data=writeback`, the rename can survive but the new file can be zero-length after crash.
3. **`_LEAD_WRITE_LOCKS` module-global dict grows unbounded** for the life of the process. For a one-shot CLI this is fine; for any future long-running/daemon mode it's a memory leak.

## Findings

- Security review P2 #7: "cache the raw robots.txt body + fetch timestamp + resolved IP; on load verify current-resolved IP matches cached IP, OR shorten TTL to 1h for disallow-decisions."
- Data-integrity review #1: "plan does NOT fsync parent directory after os.replace."
- Data-integrity review N1: "_LEAD_WRITE_LOCKS grows unbounded across long-running processes."
- Architecture review #5: "scope lock map to the `discover_jobs` call via a passed-in `LockRegistry`."

## Proposed Solutions

### Option 1: Targeted fixes

**1a. Robots cache:**
- Add `robots-cache-clear` CLI command (1-line: `os.remove(path)`).
- Shorten TTL for disallow-decisions to 1h; keep 24h for allow.
- Store resolved IP in cache entry; on load, if re-resolved IP differs, invalidate.

**1b. Parent-dir fsync in `write_json`:**
```python
# After os.replace(tmp_path, path):
try:
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
except (OSError, AttributeError):
    pass  # Not supported on this platform (e.g., Windows)
```

**1c. `_LEAD_WRITE_LOCKS`:**
- Use `WeakValueDictionary` so unused locks get collected.
- OR scope locks to the `discover_jobs` invocation via a `LockRegistry` passed in.
- OR add `_reset_for_testing()` helper (simplest; matches Kieran's suggestion).

**Pros:** All three are small, contained, correct.
**Cons:** Slightly more code; platform-specific fsync branch.
**Effort:** Small.
**Risk:** Low.

### Option 2: Defer #1a and #1c, keep #1b only

**Approach:** fsync fix is non-negotiable for correctness on Linux. Defer robots-clear and lock-leak to batch 4.

**Pros:** Smaller diff.
**Cons:** Robots poison persistence stays for the full 24h.
**Risk:** Low-medium.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §write_json code block, §utils.py RobotsCache
- `src/job_hunt/utils.py` (write_json, RobotsCache, optionally LockRegistry)

## Acceptance Criteria

- [ ] `write_json` fsyncs parent directory after `os.replace` (with platform fallback).
- [ ] Robots cache stores resolved IP; invalidates on change.
- [ ] `robots-cache-clear` CLI exists OR documented workaround (`rm data/discovery/robots_cache.json`).
- [ ] `_LEAD_WRITE_LOCKS` has `_reset_for_testing()` helper OR uses WeakValueDictionary.
- [ ] Test: `test_write_json_fsyncs_parent_dir` (best-effort; hard to assert without trace).
- [ ] Test: `test_robots_cache_invalidates_on_resolved_ip_change`.

## Work Log

### 2026-04-16 - Post-deepen review

**By:** security-sentinel, data-integrity-guardian, architecture-strategist

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- ext4 rename/data-loss issue (Ted Ts'o): https://lwn.net/Articles/322823/
