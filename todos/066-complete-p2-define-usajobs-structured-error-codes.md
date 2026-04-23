---
status: complete
priority: p2
issue_id: "066"
tags: [code-review, plan-review, usajobs, structured-errors, cli]
dependencies: []
---

# Define USAJOBS auth/config structured errors before implementation

## Problem Statement

The plan requires explicit remediation for missing or invalid USAJOBS credentials, but it does not yet say which layer owns those failures or which structured error codes will represent them.

This matters because the repo’s CLI contract depends on frozen, machine-actionable error catalogs. If USAJOBS is implemented without explicit error ownership and enums, the most likely outcome is generic network failures instead of usable remediation.

## Findings

- The plan calls for explicit remediation on missing or invalid USAJOBS credentials in [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:197](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:197) and [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:575](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md:575).
- Current discovery and ingestion error catalogs do not include provider-config/auth-specific variants for this case.

## Proposed Solutions

### Option 1: Add exact USAJOBS config/auth error codes to the plan

**Approach:** Name the new error codes, define which module owns them, and require enum-coverage tests in the same slice as the provider.

**Pros:**
- Matches the repo’s structured error contract
- Prevents generic failure leakage

**Cons:**
- Slightly more detailed planning

**Effort:** 30-60 minutes

**Risk:** Low

---

### Option 2: Reuse generic config/auth error codes if they already exist elsewhere

**Approach:** If a broader discovery config/auth code set is introduced, explicitly map USAJOBS onto that set instead of creating provider-specific variants.

**Pros:**
- Fewer enum values

**Cons:**
- Still requires explicit ownership and mapping

**Effort:** 30-60 minutes

**Risk:** Low

## Recommended Action

Completed by assigning USAJOBS config/auth failures to the discovery layer and naming the required structured error codes directly in the plan.

## Technical Details

**Affected files:**
- `/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md`
- future discovery/ingestion error-code enums

## Resources

- Plan: [/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-21-001-feat-expand-official-job-api-integrations-plan.md)

## Acceptance Criteria

- [ ] The plan names the error-owner layer for USAJOBS auth/config failures
- [ ] Structured error codes are defined or explicitly mapped before provider implementation
- [ ] Tests cover enum membership and machine-actionable remediation

## Work Log

### 2026-04-22 - USAJOBS Error Contract Defined

**By:** Codex

**Actions:**
- Updated the plan so USAJOBS readiness uses explicit `profile_missing`, `credentials_missing`, and `ready` states.
- Added discovery-owned structured error codes `usajobs_profile_missing`, `usajobs_credentials_missing`, and `usajobs_auth_invalid` to the plan’s implementation notes and error-handling section.
- Closed this todo because the plan now names both the owner layer and the concrete machine-actionable codes.

**Learnings:**
- The discovery layer is the right place to own provider-specific config/auth readiness because it keeps failure handling agent-usable before network fetches begin.

### 2026-04-21 - Review Synthesis

**By:** Codex

**Actions:**
- Converted the USAJOBS structured-error gap into a tracked todo

**Learnings:**
- USAJOBS is not just a provider addition; it also adds the first provider-specific auth/config branch in this discovery plan
