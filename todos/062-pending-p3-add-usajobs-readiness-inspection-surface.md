---
status: pending
priority: p3
issue_id: "062"
tags: [code-review, plan-review, usajobs, agent-native, cli]
dependencies: []
---

# Add an agent-usable USAJOBS readiness inspection surface

## Problem Statement

The plan requires local USAJOBS config and named search profiles, but it does not yet define how an agent can inspect readiness non-interactively before a discovery run.

This matters because “missing profile” and “missing credentials” are different remediation paths. Without a machine-readable inspection surface, USAJOBS setup becomes a hidden manual debugging step.

## Findings

- The plan requires a named USAJOBS search profile and local config for `User-Agent` and `Authorization-Key` in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:198](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:198) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:331](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:331).
- Phase 1 mentions validation and remediation text, but it does not name a JSON-visible readiness check or structured distinction between the likely failure modes.

## Proposed Solutions

### Option 1: Add explicit validation output to existing commands

**Approach:** Extend `discover-jobs` validation or a related config-check command so agents can see `profile_missing`, `credentials_missing`, or `ready` in structured JSON.

**Pros:**
- Minimal new surface area
- Good agent ergonomics

**Cons:**
- Slightly expands an existing command

**Effort:** 30-60 minutes

**Risk:** Low

---

### Option 2: Add a dedicated USAJOBS readiness/list command

**Approach:** Define a separate introspection command for configured profiles and readiness state.

**Pros:**
- Very explicit
- Easier to test and document

**Cons:**
- Extra command surface for one provider

**Effort:** 1-2 hours

**Risk:** Low

## Recommended Action

To be filled during triage.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)

## Acceptance Criteria

- [ ] The plan specifies a machine-readable readiness surface for USAJOBS
- [ ] The design distinguishes missing profile vs missing credentials vs ready
- [ ] The remediation path is agent-usable without hidden manual diagnosis

## Work Log

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Captured the USAJOBS readiness gap from the agent-native review pass
- Converted it into a lower-priority todo because it improves ergonomics rather than blocking the plan outright

**Learnings:**
- USAJOBS is the only new provider in this plan that clearly needs a local-readiness contract
