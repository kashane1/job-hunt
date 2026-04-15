---
title: Bootstrap an agent-first job hunt repository without overbuilding it
date: 2026-04-15
module: repository_bootstrap
problem_type: workflow_issue
component: development_workflow
symptoms:
  - Greenfield repo with no structure for profile data, lead tracking, or application reporting
  - Risk of drifting into a generic agent framework instead of a job-hunt operating repo
  - Need to preserve strict trust policies before browser automation begins
root_cause: Missing initial repository conventions for safety policy, artifact schemas, and a file-backed execution flow
tags:
  - bootstrap
  - workflow
  - job-hunt
  - agent-first
  - file-backed
  - safety
severity: high
---

# Bootstrap an agent-first job hunt repository without overbuilding it

## Problem

A brand new `job-hunt` repository needed to support candidate-profile ingestion, job scoring, draft generation, browser-driven application workflows, and detailed reporting. The main risk was building a generic AI framework instead of a focused operating repo for one person's job search.

## Root Cause

The repo had no starting conventions for:
- where source documents should live
- how normalized candidate context should be stored
- how leads, drafts, reports, and run summaries should be represented
- how trust policies should gate browser execution

Without those decisions up front, future automation would have been inconsistent and hard to audit.

## Solution

Build the first version as a file-backed operating repo with explicit safety defaults and a small standard-library Python toolchain.

### Key design choices

- Keep the architecture repo-native instead of building a web app first
- Separate job fit scoring from application quality scoring
- Require human approval before every final submit in v1
- Disable speculative facts by default
- Enforce browser tab budgets in policy, not just in prompts

### Core policy example

```yaml
approval_required_before_submit: true
allow_auto_submit: false
answer_policy: strict
allow_speculative_answers: false
browser_tabs_soft_limit: 10
browser_tabs_hard_limit: 15
```

### Core implementation pattern

1. Normalize raw profile docs into structured JSON artifacts
2. Extract each job into one normalized lead schema
3. Score fit with transparent weighted signals
4. Build an application draft with provenance and missing-fact tracking
5. Write both JSON and markdown reports for every attempt
6. Summarize runs for later calibration

### Why this worked

- It created immediate operational value without needing a database or UI
- It preserved a clear trust boundary before live browser submission
- It kept later browser automation flexible by moving key behavior into config and schemas

## Prevention

When starting similar agent-heavy repos:

- write `AGENTS.md` before adding automation
- make safety policies explicit in tracked config
- define schemas early so reports stay machine-readable
- prefer small local utilities over large framework dependencies for v1
- add example artifacts and end-to-end tests before expanding scope

## References

- `README.md`
- `AGENTS.md`
- `config/runtime.yaml`
- `src/job_hunt/core.py`
- `docs/plans/2026-04-15-001-feat-agent-first-job-hunt-system-plan.md`
