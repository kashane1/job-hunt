---
status: pending
priority: p2
issue_id: "038"
tags: [code-review, agent-native, batch-3]
dependencies: []
---

# Agent-native completeness: discovery-state queries, watchlist-validate, YAML-injection test

## Problem Statement

Agent-native v2 review found 3 remaining gaps (after v1 closed 5):

1. **`discovery-state` exposes cursor only, not run artifacts.** Agents can't ask "what landed in `skipped_by_robots` last run?" without reading files directly.
2. **No pre-write watchlist validation.** `watchlist-add` validates in-memory config before writing, but an agent cannot dry-run a proposed YAML without mutating disk.
3. **No test asserting YAML injection is rejected on watchlist writes** (already captured in todo #029 but the test name needs to be listed here as an acceptance criterion).

## Findings

Agent-native review:
- 9/12 PASS; 3 real gaps remain.
- Gap 1: `discovery-state` needs `--last-run` / `--bucket` filters.
- Gap 2: `watchlist-validate PATH` OR `watchlist-add --dry-run`.

## Proposed Solutions

### Option 1: Extend `discovery-state` + add `watchlist-validate`

**Approach:**
```bash
# New flags on discovery-state
python3 scripts/job_hunt.py discovery-state --last-run
python3 scripts/job_hunt.py discovery-state --last-run --bucket failed
python3 scripts/job_hunt.py discovery-state --last-run --bucket skipped_by_robots

# New command
python3 scripts/job_hunt.py watchlist-validate
python3 scripts/job_hunt.py watchlist-validate --watchlist config/watchlist-experimental.yaml

# New flag on watchlist-add (alternative to watchlist-validate)
python3 scripts/job_hunt.py watchlist-add --name NewCo --greenhouse newco --dry-run
```

`discovery-state --last-run` reads the most recent `data/discovery/history/*.json` (lexicographic sort). `--bucket X` filters outcomes to that bucket only.

`watchlist-validate` loads the YAML, runs schema validation + `watchlist.passes()` sanity checks, emits JSON `{valid: bool, errors: [...], warnings: [...]}`.

**Pros:** Completes agent-native surface. Lets agents compose discover-jobs + introspection + watchlist mutation safely.
**Cons:** 2-3 more CLI commands/flags.
**Effort:** Small-medium.
**Risk:** Low.

### Option 2: Document as batch-4 candidates

**Approach:** Ship batch 3 without these; document the workarounds (agents read `data/discovery/history/<ts>.json` directly, watchlist-add followed by git-revert).

**Pros:** Smaller batch 3.
**Cons:** Agent workflows become multi-step with file-reading hacks.
**Risk:** Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §CLI surface, §Phase 4 Deliverables

## Acceptance Criteria

- [ ] Plan adds `discovery-state --last-run` and `--bucket` flags (or explicit deferral).
- [ ] Plan adds `watchlist-validate` OR `watchlist-add --dry-run` (or explicit deferral).
- [ ] Each new command/flag has a test in Phase 4.
- [ ] Stdout JSON shape documented.

## Work Log

### 2026-04-16 - Agent-native review gaps

**By:** agent-native-reviewer

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- AGENTS.md
