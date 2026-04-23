---
status: complete
priority: p2
issue_id: "054"
tags: [code-review, plan, architecture, ingestion, glassdoor]
dependencies: []
---

# Separate Glassdoor browser automation from global allowlist widening

## Problem Statement

The original plan treated adding `glassdoor.com` to
`config/domain-allowlist.yaml` as a routine browser-lane step.

In this repo, that allowlist affects ingestion and discovery hard-fail posture,
so the change is broader than the playbook surface alone.

## Findings

- `config/domain-allowlist.yaml` is consumed by login-wall checks in
  `ingestion.py` and `discovery.py`.
- A Glassdoor allowlist entry would widen more than the browser lane unless the
  same rollout audits those side effects.
- The plan has been updated to keep allowlist promotion conditional and
  separately reviewed.

## Proposed Solutions

### Option 1: Keep first slice on manual/local intake only

**Approach:** Land browser automation without touching the global allowlist.

**Pros:**
- Narrowest initial blast radius
- Avoids accidental fetch/discovery policy drift

**Cons:**
- Does not reopen generic Glassdoor ingestion immediately

**Effort:** 2-4 hours

**Risk:** Low

---

### Option 2: Promote allowlist in the same rollout with explicit tests

**Approach:** Add `glassdoor.com` to the allowlist and update
ingestion/discovery docs and tests in the same slice.

**Pros:**
- Single coherent policy change
- Full behavior is explicit and tested

**Cons:**
- Larger change surface

**Effort:** 4-6 hours

**Risk:** Medium

## Recommended Action

Resolved by keeping `glassdoor.com` out of `config/domain-allowlist.yaml` in
the first slice and documenting Glassdoor support as manual/local intake plus
browser automation only.

## Technical Details

**Affected files:**
- `config/domain-allowlist.yaml`
- `src/job_hunt/ingestion.py`
- `src/job_hunt/discovery.py`
- related tests

## Acceptance Criteria

- [ ] The browser lane and global allowlist scope are treated as separate
      decisions
- [ ] If allowlist promotion happens, ingestion/discovery side effects are
      documented and tested in the same rollout

## Work Log

### 2026-04-20 - Review finding created

**By:** Codex

**Actions:**
- Reviewed allowlist effects across the login-wall pipeline
- Identified that the original plan widened ingestion/discovery by accident
- Updated the plan to make allowlist promotion conditional

**Learnings:**
- In this repo, domain allowlists are wider policy levers than surface
  playbooks alone

### 2026-04-21 - Resolution

**By:** Codex

**Actions:**
- Left the global login-wall allowlist unchanged
- Added docs clarifying that Glassdoor automation does not widen ingestion/discovery behavior
- Shipped the browser lane through board/surface routing instead of config allowlist mutation

**Learnings:**
- Separating browser-lane support from fetch/discovery scope kept this rollout much easier to reason about
