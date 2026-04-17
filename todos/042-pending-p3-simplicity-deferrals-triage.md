---
status: pending
priority: p3
issue_id: "042"
tags: [code-review, simplicity, batch-3, scope]
dependencies: []
---

# Simplicity deferrals to revisit before implementation (triage decision)

## Problem Statement

Simplicity reviewer (post-deepen pass) flagged 9 v2 additions as speculative. Most of v2's deepening expanded the plan from ~6-7 sessions to ~9 sessions — a ~30-50% growth. Some of that growth is load-bearing (security fixes, concurrency correctness). Some may be preemptive.

This todo documents the contested deferrals for triage decision. Separate from the non-contested items (P1s/P2s) because the plan author already considered and made choices; reviewer disagrees on scope, not correctness.

## Findings

**Simplicity reviewer's "defer to batch 4" recommendations:**

1. **Persistent robots cache** at `data/discovery/robots_cache.json` with 24h TTL. Daily run → per-run cache sufficient. ~25s savings only matters for multi-run-per-day use.
2. **`schemas/discovery-cursor.schema.json`**. Cursor is an internal file; defensive programming against a bug not yet seen.
3. **`schemas/discovery-review.schema.json`**. Same argument for review entries (plus paired-file tradeoff, see todo #035).
4. **`watchlist-show`, `watchlist-add`, `watchlist-remove`, `review-list`.** User can edit YAML / `ls`. Keep only `review-promote` / `review-dismiss` (non-trivial mutations).
5. **`--reset-cursor` flag.** `rm data/discovery/state.json` works; `jq` can delete a single key.
6. **Per-lead `_LEAD_WRITE_LOCKS` map.** Within-run dedup routes each lead_id to one worker. One module-level Lock would suffice.
7. **`--score-concurrency` flag.** Hardcode 3; expose in batch 4 if tuning need appears.
8. **`DiscoveryResult` dataclass + `to_dict()` glue.** Plain dict serializes directly.
9. **Anti-bot challenge detection regex.** A plain 403 would be retried anyway — no, this is actually load-bearing (see todo #039 for the real concern).

**Reviewer's own load-bearing items (keep):**
- JSON-LD + ATS-subdomain heuristics
- Robots stampede Event + BOM strip + spec-correct 5xx
- DNS-pin HTTPConnection
- SOURCE_NAME_MAP + consistency test (externally-visible vocabularies already committed)

## Proposed Solutions

### Option 1: Accept all 8 deferrals

**Approach:** Cut items 1-8 from batch 3 scope. Batch 3 ships with in-memory-only robots cache, no schema for cursor or reviews, minimal CLI surface (discover-jobs + review-promote/dismiss), no --reset-cursor, single-module lock, hardcoded score-concurrency, plain dict for DiscoveryResult.

Resulting scope: ~7.5 sessions (down from 9).
Removed LOC: ~200 production, 4 CLI commands, 2 schemas, 1 test file.

**Pros:** Tighter batch 3. Faster to ship.
**Cons:** Agent-native completeness loses ground (contradicts v1 agent-native reviewer).
**Effort:** Plan rewrite.
**Risk:** Low if deferred items actually ship in batch 4.

### Option 2: Accept partial deferrals

**Approach:** Cut items 1, 2, 3, 5, 6, 7, 8. Keep items 4's watchlist-show and review-list (read-only introspection is cheap; tests are thin); keep --reset-cursor for --reset-cursor ExampleCo:* globs (rm-and-rescan doesn't cover this).

**Pros:** Preserves most agent-native gains; still trims ~20% of scope.
**Effort:** Plan rewrite.
**Risk:** Low.

### Option 3: Reject all deferrals (keep as-is)

**Approach:** The plan author already considered simplicity in the "Rejected Deepening Suggestions" section. Agent-native completeness and defensive defaults are the contract.

**Pros:** Ship the plan as deepened.
**Cons:** 9 sessions is a lot.
**Risk:** Low but slower.

## Recommended Action

(Filled during triage.)

**Author's preference (needs user approval):** Option 2 — keep the agent-native surface largely intact but defer the performance-oriented additions (persistent robots cache, --score-concurrency flag, discovery-cursor schema). Save 1-1.5 sessions.

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` (significant rewrite if Option 1 or 2 chosen)

## Acceptance Criteria

- [ ] Triage decision recorded (Option 1, 2, or 3).
- [ ] If Option 1 or 2: plan updated with deferred items moved to "Batch 4 candidates."
- [ ] If deferred: "Rejected Deepening Suggestions" section updated to explain why earlier agents' requests for these are now on hold.

## Work Log

### 2026-04-16 - Simplicity review (second pass)

**By:** code-simplicity-reviewer

**Findings:** Nine v2 additions flagged as speculative. Author's rationale sound for some; reviewer's argument valid for others. Needs human judgment call.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` (§Enhancement Summary, §Rejected Deepening Suggestions)
- Similar scope-contested todo from batch 2: `todos/025-pending-p3-simplicity-contested-deferrals.md`
