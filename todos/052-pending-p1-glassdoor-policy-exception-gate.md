---
status: pending
priority: p1
issue_id: "052"
tags: [code-review, plan, security, policy, glassdoor]
dependencies: []
---

# Require explicit Glassdoor policy exception before implementation

## Problem Statement

The Glassdoor automation plan cites Terms of Use language that prohibits
automated agents on the service, but the original plan moved straight into
implementation as if the human-submit invariant fully mitigated that risk.

That makes the work plan unsafe to execute because the repo would be treating a
direct policy conflict as an ordinary engineering tradeoff.

## Findings

- Review found the plan's cited ToS language conflicts directly with a standard
  `glassdoor_easy_apply` automation surface.
- The repo's human-submit invariant helps with final-action risk, but it does
  not remove the broader "no automated agents" policy language already quoted
  in the plan.
- The plan has now been updated to add a service-specific policy gate before
  implementation starts.

## Proposed Solutions

### Option 1: Explicit policy exception before coding

**Approach:** Record a board-specific policy exception for Glassdoor-hosted
automation, then proceed with implementation only if approved.

**Pros:**
- Matches the current facts in the plan
- Keeps implementation work honest and reviewable

**Cons:**
- Adds a non-code gate before feature work

**Effort:** 1-2 hours

**Risk:** Low

---

### Option 2: Fall back to manual-assist-first plan

**Approach:** Use the conservative Glassdoor plan instead of browser
automation.

**Pros:**
- Lowest policy risk
- Uses an already-drafted fallback path

**Cons:**
- Does not deliver the requested full automation lane

**Effort:** 1-2 hours

**Risk:** Low

## Recommended Action

To be filled during triage.

## Technical Details

**Affected files:**
- `docs/plans/2026-04-20-002-feat-glassdoor-human-submit-automation-plan.md`
- `docs/plans/2026-04-20-001-feat-glassdoor-board-support-plan.md`

## Resources

- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
- [Glassdoor Terms of Use](https://www.glassdoor.com/about/terms/)

## Acceptance Criteria

- [ ] A clear policy decision exists for Glassdoor-hosted automation
- [ ] The implementation path is blocked unless that decision is approved
- [ ] The fallback path is documented if the exception is not approved

## Work Log

### 2026-04-20 - Review finding created

**By:** Codex

**Actions:**
- Reviewed the Glassdoor automation plan with multiple reviewer passes
- Identified the direct ToS conflict as a P1 planning issue
- Updated the plan to require an explicit policy exception gate

**Learnings:**
- Human-submit protects the commit action, but not necessarily the broader
  automated-agent policy language on the site
