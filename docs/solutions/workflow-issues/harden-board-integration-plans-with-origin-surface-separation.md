---
title: Harden board-integration plans with origin/surface separation and prohibited-origin invariants
date: 2026-04-19
module: planning_and_application_architecture
problem_type: workflow_issue
component: documentation
symptoms:
  - A new job-board plan tries to reuse an existing automation pipeline
  - The target board has stricter anti-automation or anti-scraping rules than current supported boards
  - The draft plan mixes "where the lead came from" with "how the application executes"
  - The draft plan proposes new enum states without an explicit consumer-first rollout
root_cause: The initial plan treated board support primarily as a routing problem, but it was also a policy-boundary problem and a schema-rollout problem. Without a hard separation between origin board and execution surface, the design drifted toward accidental automation on prohibited origins. Without a consumer-first rollout, new enum states would have broken existing validators and runtime state handling.
tags:
  - planning
  - workflow
  - linkedin
  - application-pipeline
  - schema-evolution
  - policy-boundaries
  - board-adapters
severity: high
---

# Harden board-integration plans with origin/surface separation and prohibited-origin invariants

## Problem

While deepening and reviewing the LinkedIn integration plan, three structural
gaps emerged:

1. The plan expected LinkedIn-origin jobs to reuse Greenhouse/Lever/Workday/
   Ashby playbooks, but it did not define a source of truth for the final ATS
   URL if LinkedIn URLs themselves remain hard-failed.
2. The proposed adapter API included a generic remote-ingestion hook that would
   have made it easy to reintroduce LinkedIn scraping under a cleaner
   abstraction.
3. The plan described schema evolution as "optional fields first" even though
   the LinkedIn path required new enum members like `linkedin_easy_apply_assisted`,
   `paused_manual_assist`, and `awaiting_human_action`.

The result was a plan that was directionally good but still unsafe to
implement incrementally.

## Root Cause

The draft plan conflated three different concerns:

- **Origin board**: where the lead came from
- **Execution surface**: which host/playbook actually performs the apply flow
- **Policy boundary**: whether the repo is allowed to inspect or automate the
  origin host at all

That conflation created two kinds of design drift:

1. A routing abstraction that quietly reopened a prohibited-origin boundary
2. A phased rollout that assumed all changes were additive fields, when some
   were new required states

## Solution

### 1. Separate origin board from execution surface

Use:

- `origin_board` for provenance
- `surface` for the real execution host/playbook

Examples:

- LinkedIn-origin job redirecting to Greenhouse:
  - `origin_board=linkedin`
  - `surface=greenhouse_redirect`
- LinkedIn-hosted Easy Apply:
  - `origin_board=linkedin`
  - `surface=linkedin_easy_apply_assisted`

This preserves reuse of existing ATS playbooks without pretending LinkedIn is
itself an automation surface.

### 2. Preserve prohibited-origin boundaries as hard invariants

For boards with stricter policy than the currently supported stack:

- keep the origin host out of fetch-based ingestion/discovery
- forbid executor automation on the origin host
- treat origin-hosted flows as manual-assist only

For the LinkedIn plan, that became:

- `linkedin.com` stays outside `config/domain-allowlist.yaml`
- `ingest-url` and `discover-jobs` continue to hard-fail LinkedIn
- no executor/playbook may automate `linkedin.com`

### 3. Require deterministic manual intake for redirect reuse

If the origin host cannot be fetched or inspected, redirect reuse must come
from the intake artifact itself.

Required contract:

- either `application_url` / `canonical_url` already points at the final ATS
  host
- or the local/manual artifact includes a sanitized `redirect_chain` ending at
  the final ATS host

If neither exists, the draft is not ATS-automatable and falls back to the
manual-assist surface.

### 4. Split adapter APIs so remote ingestion is optional

Do not make remote HTML/URL ingestion part of the default board adapter
contract.

Instead:

```python
class BoardAdapter(Protocol):
    def resolve_application_target(self, lead: dict) -> dict: ...
    def normalize_manual_intake(self, metadata: dict) -> dict: ...

class RemoteIngestionAdapter(BoardAdapter, Protocol):
    def ingest_remote_metadata(self, url: str, html_text: str | None = None) -> dict: ...
```

This lets boards like Indeed support fetch-based ingestion while boards like
LinkedIn explicitly do not implement the remote-ingestion extension.

### 5. Roll out new enum states consumer-first

Optional-field guidance is not enough when the plan introduces new required
enum/state members.

Before any producer writes new LinkedIn artifacts, update:

- schemas
- `_PRIORITY_LADDER`
- `lead_state_from_attempt()`
- event handling
- fixtures/tests

Only after those consumers accept the new states may the repo emit:

- `surface=linkedin_easy_apply_assisted`
- `status=paused_manual_assist`
- `lifecycle_state=awaiting_human_action`

## Minimal Working Pattern

When adding a new job board with stronger restrictions than the current stack:

1. Keep the board as an **origin board** first.
2. Reuse existing playbooks only when the execution host is already a supported
   ATS.
3. Make manual/local intake carry the final execution target if the origin host
   cannot be fetched.
4. Encode prohibited-origin rules as hard invariants, not soft defaults.
5. Land enum/state consumers before producers.

## Prevention

Before approving any future board-integration plan, ask these questions:

1. Can the repo legally/policy-wise fetch or inspect the origin host?
2. If not, where does the final execution URL come from?
3. Are we separating `origin_board` from execution `surface`?
4. Does the adapter contract accidentally make prohibited-origin scraping easy?
5. Are any new states or enums being introduced, and if so, have consumers
   been updated first?

If any answer is unclear, the plan is not implementation-ready yet.

## Related

- [human-in-the-loop-on-submit-as-tos-defense.md](../security-issues/human-in-the-loop-on-submit-as-tos-defense.md)
- [indeed-surface-detection-via-directapply.md](../integration-issues/indeed-surface-detection-via-directapply.md)
- [extend-cli-with-new-modules-without-breaking-backward-compat.md](extend-cli-with-new-modules-without-breaking-backward-compat.md)
- [ship-tolerant-consumers-before-strict-producers.md](ship-tolerant-consumers-before-strict-producers.md)
- Plan refined using this pattern:
  [2026-04-19-001-feat-linkedin-and-board-adapters-plan.md](../../plans/2026-04-19-001-feat-linkedin-and-board-adapters-plan.md)
