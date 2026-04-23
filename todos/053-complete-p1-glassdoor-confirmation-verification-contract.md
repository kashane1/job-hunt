---
status: complete
priority: p1
issue_id: "053"
tags: [code-review, plan, security, confirmation, glassdoor]
dependencies: []
---

# Close Glassdoor confirmation spoofing before confirmed-state support

## Problem Statement

The original plan included confirmation capture in the core Glassdoor rollout
while deferring Glassdoor sender verification to later work.

That leaves a state-poisoning path where unverified confirmation-like mail
could advance application status incorrectly.

## Findings

- Current confirmation handling depends on sender allowlist, DKIM, and body
  correlation rules.
- The original Glassdoor plan did not make those controls mandatory in the same
  rollout.
- The plan has now been tightened so the first slice ends at
  `submitted_provisional` unless verified Glassdoor confirmation support lands
  in the same slice.

## Proposed Solutions

### Option 1: Keep v1 at submitted_provisional

**Approach:** Do not support email-driven Glassdoor confirmation yet.

**Pros:**
- Safest first slice
- Removes spoofing risk from the initial rollout

**Cons:**
- Does not close the lifecycle fully in v1

**Effort:** 1-2 hours

**Risk:** Low

---

### Option 2: Add verified confirmation support in the same rollout

**Approach:** Extend `confirmation.py` with Glassdoor allowlist, DKIM checks,
body correlation, and tests.

**Pros:**
- Delivers a complete confirmed-state lifecycle
- Matches existing Indeed posture

**Cons:**
- Requires real sample messages or strong evidence for verification rules

**Effort:** 4-8 hours

**Risk:** Medium

## Recommended Action

Resolved via the safer first-slice default: Glassdoor stays at
`submitted_provisional` and defers email-driven confirmation promotion until
verified sender rules land with tests.

## Technical Details

**Affected files:**
- `docs/plans/2026-04-20-002-feat-glassdoor-human-submit-automation-plan.md`
- `src/job_hunt/confirmation.py`
- `tests/test_phase8_confirmation.py`

## Resources

- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`

## Acceptance Criteria

- [ ] Glassdoor first-slice status policy is explicit: provisional-only or
      verified-confirmation-in-slice
- [ ] Unverified Glassdoor-like messages cannot advance state
- [ ] Tests prove quarantine behavior for unverified messages

## Work Log

### 2026-04-20 - Review finding created

**By:** Codex

**Actions:**
- Reviewed the confirmation section of the Glassdoor automation plan
- Identified deferred sender verification as a P1 gap
- Updated the plan to require either provisional-only lifecycle or same-slice
  verification rules

**Learnings:**
- Confirmation support is security-sensitive enough that "follow-up later" is
  not an acceptable core-plan default

### 2026-04-21 - Resolution

**By:** Codex

**Actions:**
- Added the provisional-only rule to the Glassdoor playbook and operator guide
- Added pipeline coverage asserting the shipped Glassdoor lane ends at `submitted_provisional`
- Left `confirmation.py` unchanged for Glassdoor in this slice to avoid spoofable state promotion

**Learnings:**
- Choosing the safer lifecycle up front let the rollout stay narrow without leaving a hidden security gap
