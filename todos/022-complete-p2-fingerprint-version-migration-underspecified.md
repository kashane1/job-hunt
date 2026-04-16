---
status: pending
priority: p2
issue_id: "022"
tags: [code-review, data-integrity, migration, batch-2]
dependencies: []
---

# fingerprint_version migration has no script, no trigger, no collision handling

## Problem Statement

The plan adds `fingerprint_version: "v2"` field on leads with the promise that "future canonicalization changes trigger one-shot migration." But the migration itself is never specified: no deliverable for a migration script, no CLI command, no collision handling, no rollback, no treatment of pre-existing leads missing the field.

This is classic under-documented migration — a promise without a landing place.

## Findings

### What's specified

- Schema: `fingerprint_version` as optional field on lead (line 186)
- Code: `FINGERPRINT_VERSION = "v2"` constant; written on every ingested lead
- Prose: "A one-shot migration script recomputes fingerprints when the version is bumped"

### What's missing

1. **No deliverable for the migration script** — no path (e.g., `scripts/migrate_fingerprints.py`), no CLI command (e.g., `migrate-fingerprints`), no test.

2. **No collision handling during migration** — if v3 canonicalization merges two previously-distinct leads into the same fingerprint, which `lead_id` wins? What happens to the merged lead's downstream content records (`content.lead_id` foreign key)?

3. **No treatment of leads missing `fingerprint_version`** — batch-1 leads have no field. Are they implicitly "v1"? Skipped? Migrated?

4. **No check-integrity integration** — `check-integrity` doesn't flag leads with stale `fingerprint_version`, so there's no automated trigger condition for the migration.

5. **No backup/rollback** — rewriting every lead file is destructive. No pre-migration snapshot specified.

## Proposed Solutions

### Option 1: Full migration spec OR defer the field entirely (Recommended — defer)

Given the simplicity review already flagged this as premature (the canonicalizer has shipped exactly once, no history of changes), the cheapest fix is to **defer** the field until there's an actual version-2 canonicalizer.

**Defer (Recommended):**
- Remove `fingerprint_version` from the schema and code
- Remove related Enhancement Summary item and deliverables
- Add a comment in `canonicalize_url`: "If this logic changes, add a `fingerprint_version` field and a migration script at that time"
- Saves ~15 LOC and one deliverable line

**Alternative — full spec if the field is kept:**
- Add `scripts/migrate_fingerprints.py` as explicit deliverable
- Add `migrate-fingerprints --dry-run / --apply` CLI command
- Collision handling: if multiple leads map to the same new fingerprint, preserve both with a `fingerprint_aliases` list; let the user pick which to keep
- Batch 1 leads (no field): treat as `fingerprint_version: "v1"` implicitly
- Backup: write snapshot to `data/leads/_pre_migration_<timestamp>/` before applying
- `check-integrity` flags leads whose `fingerprint_version != FINGERPRINT_VERSION`

**Effort — Defer:** Negative (removes complexity)
**Effort — Full spec:** Medium (script + CLI + collision logic + backup + integrity check)

## Recommended Action

Defer. The field is premature for day 1; the migration can be designed when a v3 canonicalizer is actually needed. The simplicity review flagged this and it aligns with YAGNI for a single-user CLI.

If the user prefers to keep the field for future-proofing, adopt the full spec — but recognize that without `check-integrity` trigger and collision handling, the field is decorative.

## Acceptance Criteria

**If deferring:**
- [ ] `fingerprint_version` removed from lead schema
- [ ] `FINGERPRINT_VERSION` constant removed from `ingestion.py`
- [ ] Enhancement Summary #4 removed
- [ ] Comment added to `canonicalize_url` documenting when to add versioning

**If keeping:**
- [ ] Migration script exists as a deliverable with path
- [ ] `migrate-fingerprints --dry-run / --apply` CLI command
- [ ] `check-integrity` flags leads with stale `fingerprint_version`
- [ ] Collision handling specified and tested
- [ ] Backup/rollback strategy documented

## Work Log

### 2026-04-16 - Discovery

**By:** data-integrity-guardian, code-simplicity-reviewer

**Actions:**
- Data integrity flagged as under-specified migration
- Simplicity reviewer flagged as premature for day 1
- Both converge on either full-spec or defer
