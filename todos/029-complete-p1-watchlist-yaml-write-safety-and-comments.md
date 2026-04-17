---
status: pending
priority: p1
issue_id: "029"
tags: [code-review, security, batch-3, watchlist, yaml]
dependencies: []
---

# Safe YAML write-back for watchlist-add/remove and comment preservation

## Problem Statement

Batch 3 adds `watchlist-add` / `watchlist-remove` CLI commands that write back to `config/watchlist.yaml`. The plan (1) has no specification for escaping user-supplied string inputs (e.g., `--notes "evil: payload\nsome_key: injected"`), and (2) `config/watchlist.example.yaml` ships WITH inline filter comments that `simple_yaml` cannot round-trip. Calling `watchlist-add` will destroy those comments on every mutation.

Two real bugs in one code path: YAML injection and silent comment loss.

## Findings

- **YAML injection surface:** `--notes "evil: payload\n- injected: true"` via a naive write path could break YAML structure or smuggle unintended keys. `simple_yaml` was extended for READ in Phase 1 (depth-2 list-of-mappings) but the write path is not detailed.
- **Comment destruction:** `simple_yaml` is line-based with no comment node — any load→mutate→dump round-trip drops comments. The shipped `watchlist.example.yaml` has comments on filter lists (e.g. `# deal-breakers — highest precedence`). User-added comments in their local `config/watchlist.yaml` would be lost on first `watchlist-add`.
- **Data-integrity confirmation:** this is Data-integrity review finding N4 and Security review finding WL-write and Agent-native review finding #2 — three agents independently flagged it.

Plan location: §Phase 4 Deliverables, "watchlist-add writes back to YAML atomically (preserves list order; replaces whole file via write_json-equivalent for YAML)."

## Proposed Solutions

### Option 1: Dedicated escaping YAML emitter + warn on comment loss

**Approach:** Write a small `watchlist._emit_yaml(data: dict) -> str` that:
- Double-quotes every string value, escapes `"` and `\n`/`\r` and control chars.
- Rejects control characters and non-printable chars in input at CLI layer (watchlist-add argument validation).
- Before overwriting: detect comment lines in the existing file; if present, warn and require `--force` OR refuse to overwrite.

**Pros:** No new dependencies. Safe escaping. User-aware comment handling.
**Cons:** Users who edit via CLI lose comments unless they pass `--force` knowingly.
**Effort:** Medium (2-3 hours).
**Risk:** Low.

### Option 2: Append-only text manipulation for watchlist-add

**Approach:** `watchlist-add` appends a new `- name: ...` block to the existing file text without round-tripping. `watchlist-remove` byte-patches the specific entry's line range.

**Pros:** Preserves ALL existing content including comments.
**Cons:** Brittle against user formatting variations; more code to test edge cases.
**Effort:** Medium (3-4 hours).
**Risk:** Medium (byte-patching regex edge cases).

### Option 3: Adopt `ruamel.yaml` as an optional `[discovery]` extra

**Approach:** Preserve comments via a real round-tripping YAML parser in an optional extra.

**Pros:** Clean solution; preserves exactly.
**Cons:** Violates "no new default deps" line batch 2/3 held; users who don't install extra see degraded behavior.
**Effort:** Small.
**Risk:** Low (but introduces dep surface).

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Phase 4 watchlist-add / watchlist-remove
- `src/job_hunt/watchlist.py` (new)
- `src/job_hunt/simple_yaml.py` (write-path companion)

## Acceptance Criteria

- [ ] Plan specifies how watchlist-add escapes string inputs (name, notes, slugs).
- [ ] Plan specifies behavior when existing file contains comments (warn / --force / preserve).
- [ ] New test: `test_watchlist_add_rejects_yaml_injection` — `--notes "evil: payload"` round-trips safely.
- [ ] New test: `test_watchlist_add_preserves_comments_or_warns` — one of the three options enforced.
- [ ] Control-character inputs (newlines, `\r`, `\x1f`) rejected at argument parsing layer.

## Work Log

### 2026-04-16 - Discovered during post-deepen review (3 agents converged)

**By:** security-sentinel, data-integrity-guardian, agent-native-reviewer

**Findings:** Three-agent convergence confirms real gap. simple_yaml lacks comment preservation; plan silent on injection escaping.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- Batch-2 YAML loader: `src/job_hunt/simple_yaml.py`
