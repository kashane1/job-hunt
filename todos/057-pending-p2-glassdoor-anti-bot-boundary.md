---
status: pending
priority: p2
issue_id: "057"
tags: [code-review, plan, security, anti-bot, glassdoor]
dependencies: []
---

# Make the Glassdoor anti-bot boundary explicit and non-evasive

## Problem Statement

The original plan said Glassdoor automation should stop on login walls,
CAPTCHA, MFA, or anti-bot challenges, but it did not explicitly forbid retry
loops or other evasive fallback behavior.

That leaves too much room for unsafe implementation drift.

## Findings

- ToS-sensitive automation needs a precise stop boundary, not just a vague
  "handle challenge" instruction.
- The original playbook outline did not forbid refresh storms, repeated
  navigation, or selector escalation after bot-defense signals.
- The plan has been updated to require a terminal attempt record and abort on
  anti-bot challenge.

## Proposed Solutions

### Option 1: Explicit abort contract

**Approach:** Document anti-bot, CAPTCHA, MFA, and login-wall detection as
terminal states that abort the attempt and batch.

**Pros:**
- Clear implementation boundary
- Easier to test and audit

**Cons:**
- Lower automation completion in difficult flows

**Effort:** 2-3 hours

**Risk:** Low

---

### Option 2: Partial retry budget

**Approach:** Allow limited automated retries after challenge detection.

**Pros:**
- Higher completion chance in flaky flows

**Cons:**
- Much higher policy and anti-bot risk
- Harder to reason about safely

**Effort:** 3-5 hours

**Risk:** High

## Recommended Action

To be filled during triage.

## Technical Details

**Affected files:**
- `playbooks/application/glassdoor-easy-apply.md`
- related apply/orchestration code
- anti-bot tests

## Acceptance Criteria

- [ ] Anti-bot, CAPTCHA, MFA, and login-wall events are terminal states
- [ ] The playbook forbids automated retries or evasive fallback behavior
- [ ] Tests verify abort behavior when challenges are detected

## Work Log

### 2026-04-20 - Review finding created

**By:** Codex

**Actions:**
- Reviewed anti-bot handling in the Glassdoor plan
- Identified that the stop boundary was underspecified
- Updated the plan to make anti-bot abort behavior explicit

**Learnings:**
- "Stop on challenge" is not enough unless the plan also forbids what happens
  next
