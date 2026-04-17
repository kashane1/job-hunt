---
status: pending
priority: p1
issue_id: "032"
tags: [code-review, batch-3, discovery, contracts]
dependencies: []
---

# Replace `...` placeholders in `Outcome.to_dict()` and `DiscoveryResult.to_dict()` with explicit shape

## Problem Statement

The plan's code blocks for `Outcome.to_dict()` and `DiscoveryResult.to_dict()` use `...` placeholders. `schemas/discovery-run.schema.json` is described as "mirrors `DiscoveryResult.to_dict()` exactly." With both sides deferred, neither is specified — classic split-brain against ourselves.

This is the exact pattern batch 2's `reconcile-plan-after-multi-agent-deepening-review.md` learning warns about: prose claims a contract, artifacts are empty.

## Findings

- Plan `§Data types` `DiscoveryResult.to_dict(self) -> dict:` body is literally `...`
- Schema description says "mirrors `DiscoveryResult.to_dict()` exactly"
- `Outcome.to_dict()` similarly deferred
- `Bucket = Literal["discovered", ...]` — JSON-serializable as bare string, but the implementation must serialize `Outcome.entry: ListingEntry | None` and `Outcome.detail: dict[str, str]` explicitly

Plan location: §Module structure lines ~556-561 (`Outcome`, `DiscoveryResult`, `SourceRun` dataclasses).

## Proposed Solutions

### Option 1: Spell out the shape in the plan

**Approach:** Add a concrete code block:
```python
@dataclass(frozen=True)
class Outcome:
    bucket: Bucket
    entry: ListingEntry | None
    detail: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "entry": _listing_entry_to_dict(self.entry) if self.entry else None,
            "detail": dict(self.detail),
        }


def _listing_entry_to_dict(e: ListingEntry) -> dict:
    return {
        "title": e.title,
        "location": e.location,
        "posting_url": e.posting_url,
        "source": e.source,
        "source_company": e.source_company,
        "internal_id": e.internal_id,
        "updated_at": e.updated_at,
        "signals": list(e.signals),
        "confidence": e.confidence,
    }


@dataclass
class DiscoveryResult:
    outcomes: list[Outcome]
    sources_run: list[SourceRun]
    run_started_at: str
    run_completed_at: str

    def to_dict(self) -> dict:
        return {
            "run_started_at": self.run_started_at,
            "run_completed_at": self.run_completed_at,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "sources_run": [s.__dict__ for s in self.sources_run],
            "counts": {b: len(self.by_bucket(b)) for b in (
                "discovered", "filtered_out", "duplicate_within_run",
                "already_known", "skipped_by_robots", "skipped_by_budget",
                "failed", "low_confidence",
            )},
        }
```

Then `discovery-run.schema.json` is authored as a concrete JSON Schema against THIS shape (not a forward-reference).

**Pros:** Eliminates split-brain. Test writers can assert structure directly.
**Cons:** ~40 lines added to plan.
**Effort:** Small.
**Risk:** Low.

### Option 2: Drop the dataclasses, use plain dicts

**Approach:** Per simplicity reviewer: `discover_jobs` returns a plain dict; no `to_dict` glue. Schema becomes the single contract.

**Pros:** Less code; no dataclass<->dict translation layer.
**Cons:** Loses type-checkable composition; every bucket key becomes stringly-typed.
**Effort:** Medium (rewrite).
**Risk:** Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Module structure, §Schema additions
- `schemas/discovery-run.schema.json` (must be authored concretely)

## Acceptance Criteria

- [ ] `Outcome.to_dict()` body shown (not `...`).
- [ ] `DiscoveryResult.to_dict()` body shown (not `...`).
- [ ] `schemas/discovery-run.schema.json` is concrete JSON Schema, not a description.
- [ ] Test: `test_discovery_result_to_dict_matches_schema` round-trips via jsonschema validate.
- [ ] Test: `test_outcome_to_dict_all_buckets` covers each Literal value.

## Work Log

### 2026-04-16 - Discovered during post-deepen review

**By:** kieran-python-reviewer, pattern-recognition-specialist

**Findings:** Circular spec — prose points to schema, schema points to method, method is `...`.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- Similar learning: `docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md`
