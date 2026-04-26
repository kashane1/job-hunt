---
title: Land official job API discovery with compatibility-first provenance
category: integration-issues
date: 2026-04-23
tags:
  - discovery
  - ashby
  - workable
  - usajobs
  - provenance
  - schemas
  - compatibility
---

# Problem

The repo needed to add Ashby, Workable, and USAJOBS discovery without
repeating earlier discovery drift where runtime source tokens, schemas,
cursor state, and operator docs fell out of sync.

# Root Cause

The original discovery stack treated provider registration, `discovered_via`
values, lead provenance, and cursor persistence as loosely related concerns.
That worked for a small source set, but it was too fragile once new official
providers and paged APIs were added.

# Solution

Ship the expansion in a compatibility-first sequence instead of bolting on
providers directly:

- Added a shared provenance contract in
  [source_provenance.py](/Users/simons/job-hunt/src/job_hunt/source_provenance.py:1)
  so `SOURCE_NAME_MAP`, precedence, `primary_source`, and `observed_sources`
  come from one place.
- Updated discovery schemas and source catalog before new providers emitted
  new source values.
- Extended watchlist support for flat `ashby`, `workable`, and
  `usajobs_search_profile` fields plus top-level `usajobs_profiles`.
- Added first-class providers in
  [ashby.py](/Users/simons/job-hunt/src/job_hunt/discovery_providers/ashby.py:1),
  [workable.py](/Users/simons/job-hunt/src/job_hunt/discovery_providers/workable.py:1),
  and
  [usajobs.py](/Users/simons/job-hunt/src/job_hunt/discovery_providers/usajobs.py:1).
- Tightened cursor/state semantics so partial runs persist resumable status and
  USAJOBS can continue from `next_cursor` instead of pretending a capped run
  completed.
- Kept `lead.source` as the compatibility alias while writing richer
  provenance fields alongside `discovered_via`.

# Prevention

When adding a new discovery source:

1. Update the source contract and schemas before the provider emits new data.
2. Centralize precedence and provenance in one helper instead of letting each
   consumer infer authority ad hoc.
3. Treat paged APIs as cursor problems from day one, not as a follow-up.
4. Keep credentials runtime-only and add explicit readiness/error states for
   auth-bound providers.

# Verification

Verified with full regression coverage:

```bash
python3 -m unittest discover -s /Users/simons/job-hunt/tests -p 'test_*.py'
```

# Related

- [Ship tolerant consumers before strict producers](/Users/simons/job-hunt/docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md:1)
- [Land multi-board application architecture with registry-owned routing](/Users/simons/job-hunt/docs/solutions/workflow-issues/land-multi-board-architecture-with-registry-owned-routing.md:1)
