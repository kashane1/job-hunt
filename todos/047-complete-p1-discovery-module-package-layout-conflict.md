---
status: complete
priority: p1
issue_id: "047"
tags: [code-review, architecture, python, discovery]
dependencies: []
---

# Fix discovery module/package naming conflict in the plan

## Problem Statement

The plan proposes keeping `src/job_hunt/discovery.py` while also adding
`src/job_hunt/discovery/base.py` and `src/job_hunt/discovery/registry.py`.
That layout is not importable in Python because `job_hunt.discovery` cannot be
both a module and a package at the same time.

This is a merge-blocking plan issue because the proposed file plan is not
implementable as written.

## Findings

- The plan's `## Proposed Module Structure` section keeps
  `src/job_hunt/discovery.py` and adds a `src/job_hunt/discovery/` package in
  the same namespace.
- Python import resolution would force a choice between the module and the
  package, breaking imports or requiring an undocumented compatibility shim.
- The phased rollout does not describe any rename, shim, or deprecation path
  for existing imports that reference `job_hunt.discovery`.

## Proposed Solutions

### Option 1: Rename the existing module during the refactor

**Approach:** Move shared orchestration from `src/job_hunt/discovery.py` to a
new package module such as `src/job_hunt/discovery/orchestrator.py`, then make
all new package modules live under `src/job_hunt/discovery/`.

**Pros:**
- Produces a clean long-term package layout
- Removes import ambiguity completely

**Cons:**
- Requires an explicit migration of current imports
- Slightly larger first refactor

**Effort:** 2-4 hours

**Risk:** Medium

---

### Option 2: Keep `discovery.py` and use a differently named package

**Approach:** Preserve `src/job_hunt/discovery.py` and put new modules in a
package such as `src/job_hunt/discovery_providers/`.

**Pros:**
- Minimal compatibility disruption
- Keeps current imports stable

**Cons:**
- Slightly less elegant naming
- Splits discovery concepts across two top-level names

**Effort:** 1-2 hours

**Risk:** Low

---

### Option 3: Add a documented compatibility shim plan

**Approach:** Keep the desired end state, but add an explicit temporary import
shim and deprecation sequence to the plan.

**Pros:**
- Preserves the intended architecture
- Makes rollout sequencing explicit

**Cons:**
- More moving parts
- Still requires a rename eventually

**Effort:** 2-3 hours

**Risk:** Medium

## Recommended Action

Resolved by moving provider registration into `src/job_hunt/discovery_providers/`
while keeping `src/job_hunt/discovery.py` as the public orchestration module.

## Technical Details

**Affected files:**
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:589)

**Related components:**
- Discovery orchestration imports
- Future discovery provider registry modules

**Database changes (if any):**
- Migration needed? No

## Resources

- **Plan:** [multi-board application architecture plan](/Users/simons/job-hunt/docs/plans/2026-04-19-004-feat-multi-board-application-architecture-plan.md:589)

## Acceptance Criteria

- [ ] The plan no longer proposes an impossible `job_hunt.discovery` module and package coexistence
- [ ] The file plan names one valid Python layout for discovery modules
- [ ] Any import-compatibility or rename sequence is explicitly documented
- [ ] The rollout order for the chosen layout is clear

## Work Log

### 2026-04-20 - Initial Discovery

**By:** Codex

**Actions:**
- Reviewed the plan with the `ce-review` workflow
- Cross-checked the proposed file layout against Python import rules
- Confirmed the current plan keeps `src/job_hunt/discovery.py` while adding a
  `src/job_hunt/discovery/` package

**Learnings:**
- This is a concrete implementation blocker, not a stylistic concern
- The plan needs a rename or alternate package name before implementation

### 2026-04-20 - Resolution

**By:** Codex

**Actions:**
- Updated the plan to use `src/job_hunt/discovery_providers/`
- Added `job_hunt.discovery_providers.base` and `.registry`
- Refactored `job_hunt.discovery` to delegate provider dispatch through the new registry
- Ran the full unittest suite successfully

**Learnings:**
- Keeping `job_hunt.discovery` as the orchestration entrypoint avoided import churn
- The provider registry landed cleanly without a module/package namespace collision

## Notes

- This should be resolved before any implementation plan is approved.
