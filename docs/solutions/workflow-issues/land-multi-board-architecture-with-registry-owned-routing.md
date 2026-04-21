---
title: Land multi-board application architecture with registry-owned routing and compatibility-safe rollout
category: workflow-issues
date: 2026-04-20
tags:
  - architecture
  - multi-board
  - registries
  - compatibility
  - linkedin
  - manual-assist
  - discovery
---

# Problem

The multi-board architecture plan was directionally right but not yet safe to
implement. It still had split ownership for routing metadata, an impossible
Python module/package layout for discovery, and no durable contract for resume
or manual-assist handoff state.

# Root Cause

The plan introduced new abstractions faster than it assigned ownership rules.
That left three concrete risks:

- `batch_eligible`, `playbook_path`, and related routing fields could drift
  across boards, surfaces, and application orchestration
- `job_hunt.discovery` was about to become both a module and a package
- persisted drafts would store routing outputs without enough context to
  explain or safely resume those decisions later

# Solution

We landed the refactor in a compatibility-first sequence:

1. Kept `ApplicationTarget` as the single resolved runtime record instead of
   adding a second near-duplicate resolution object.
2. Added `job_hunt.surfaces.registry` as the authority for:
   - `playbook_path`
   - `surface_policy`
   - `handoff_kind`
   - `batch_eligible(surface, target)`
   - cover-letter policy
3. Added `job_hunt.executors.registry` and upgraded executor metadata to typed
   capabilities without changing current behavior.
4. Added `job_hunt.discovery_providers/` and kept `job_hunt.discovery` as the
   orchestration entrypoint, avoiding the module/package collision.
5. Persisted durable routing and handoff data in `plan.json` and `status.json`:
   - `routing_snapshot`
   - `handoff_context`
   - `requires_human_submit`
6. Kept the no-auto-submit invariant explicit in both the plan and runtime
   artifacts.

Representative files:

- `src/job_hunt/surfaces/registry.py`
- `src/job_hunt/executors/registry.py`
- `src/job_hunt/discovery_providers/registry.py`
- `src/job_hunt/application.py`
- `src/job_hunt/boards/registry.py`
- `docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md`

# Prevention

Before implementing a large refactor plan, force these decisions into one pass:

1. Name exactly one authority for every routing field.
2. Check proposed file layouts against the language import model.
3. Add tolerant readers before emitting new persisted fields.
4. Make human-handoff state durable if the workflow must survive restarts.

If a refactor changes routing or policy behavior, add artifact-level fields and
tests in the same change rather than treating them as follow-up cleanup.

# Verification

Verified with focused and full test runs:

```bash
python3 -m unittest /Users/simons/job-hunt/tests/test_surfaces.py /Users/simons/job-hunt/tests/test_executors.py /Users/simons/job-hunt/tests/test_discovery_registry.py /Users/simons/job-hunt/tests/test_phase4_application.py
python3 -m unittest /Users/simons/job-hunt/tests/test_playbooks.py /Users/simons/job-hunt/tests/test_application.py
python3 -m unittest discover -s /Users/simons/job-hunt/tests
```

# Related Docs

- `docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md`
- `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md`
- `docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md`
- `docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md`
