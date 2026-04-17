---
status: pending
priority: p2
issue_id: "036"
tags: [code-review, batch-3, api-design]
dependencies: []
---

# Extract `DiscoveryConfig` dataclass; `discover_jobs` has 11 parameters

## Problem Statement

`discover_jobs(watchlist_path, leads_dir, discovery_root, max_ingest, max_workers, sources, dry_run, auto_score, score_concurrency, scoring_config, candidate_profile, reset_cursor)` — 12 parameters. Past the threshold where a config dataclass pays back more than it costs. The next time someone adds a 13th, the signature gets unreadable.

## Findings

Kieran Python review: "11 parameters is past the threshold. Extract."

## Proposed Solutions

### Option 1: Extract `DiscoveryConfig`

```python
@dataclass(frozen=True)
class DiscoveryConfig:
    max_ingest: int = 50
    max_workers: int = 3
    sources: tuple[str, ...] = ()   # empty = all
    dry_run: bool = False
    auto_score: bool = True
    score_concurrency: int = 3
    scoring_config: dict | None = None
    candidate_profile: dict | None = None
    reset_cursor: tuple[str, str] | None = None

def discover_jobs(
    watchlist_path: Path,
    leads_dir: Path,
    discovery_root: Path,
    config: DiscoveryConfig = DiscoveryConfig(),
) -> DiscoveryResult: ...
```

**Pros:** Signature goes from 12 → 4. Tests construct config once. CLI handler maps argparse → DiscoveryConfig in one place.
**Cons:** One more type to document.
**Effort:** Small.
**Risk:** Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Module structure `discover_jobs` signature

## Acceptance Criteria

- [ ] Plan's `discover_jobs` signature uses `DiscoveryConfig`.
- [ ] `DiscoveryConfig` defined as frozen dataclass.
- [ ] CLI handler maps `args` → `DiscoveryConfig` in one place.
- [ ] Test signatures still readable.

## Work Log

### 2026-04-16 - Kieran Python review P2 #5

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
