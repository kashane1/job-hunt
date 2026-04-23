---
status: complete
priority: p2
issue_id: "055"
tags: [code-review, plan, architecture, routing, glassdoor]
dependencies: []
---

# Define shared routing ownership for Glassdoor and late ATS handoffs

## Problem Statement

The original plan duplicated routing responsibility between the Glassdoor board
adapter and the generic router, and it did not define how a Glassdoor-hosted
flow that hands off to ATS after clicking Apply should be re-routed.

That would likely spill brittle branching into orchestration code.

## Findings

- The adapter originally owned per-host ATS mapping even though similar routing
  already exists centrally.
- The plan did not define who writes the handoff checkpoint when Glassdoor
  yields to ATS after the apply button is clicked.
- The plan has been updated to centralize host-to-surface routing and require
  orchestration re-resolution through the shared path.

## Proposed Solutions

### Option 1: Shared router owns host mapping

**Approach:** Keep Glassdoor adapter focused on origin-board detection and
manual-intake normalization; use shared re-resolution for ATS handoffs.

**Pros:**
- Single source of truth for host mapping
- Reduces cross-module drift

**Cons:**
- Requires a clear handoff contract in orchestration

**Effort:** 3-5 hours

**Risk:** Low

---

### Option 2: Board-specific routing inside Glassdoor adapter

**Approach:** Let the adapter own both Glassdoor and ATS final-host routing.

**Pros:**
- One module to inspect for Glassdoor behavior

**Cons:**
- Duplicates existing routing knowledge
- Higher drift risk

**Effort:** 3-5 hours

**Risk:** Medium

## Recommended Action

Resolved by centralizing ATS-host detection in shared board-routing helpers and
using that same shared resolver for Glassdoor late handoffs.

## Technical Details

**Affected files:**
- `src/job_hunt/boards/glassdoor.py`
- `src/job_hunt/boards/registry.py`
- `playbooks/application/generic-application.md`
- orchestration/apply flow code

## Acceptance Criteria

- [ ] ATS host mapping has one authoritative owner
- [ ] Late Glassdoor-to-ATS handoff is recorded with an explicit checkpoint
- [ ] `origin_board=glassdoor` survives the handoff

## Work Log

### 2026-04-20 - Review finding created

**By:** Codex

**Actions:**
- Reviewed routing ownership in the Glassdoor plan
- Identified duplicated host mapping and missing handoff semantics
- Updated the plan to centralize routing ownership

**Learnings:**
- Late reroute paths need explicit checkpoint contracts or they leak complexity
  into unrelated modules

### 2026-04-21 - Resolution

**By:** Codex

**Actions:**
- Added shared routing helpers for Greenhouse/Lever/Workday/Ashby host detection
- Reused those helpers from both the board registry and the LinkedIn/Glassdoor adapters
- Added the `ats_redirect_handoff` playbook checkpoint and Glassdoor reroute tests

**Learnings:**
- Pulling ATS host matching into one helper removed the duplication risk the plan called out
