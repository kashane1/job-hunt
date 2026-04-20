---
title: "feat: Refactor into a reusable multi-board application architecture"
type: feat
status: completed
date: 2026-04-19
deepened: 2026-04-20
---

# feat: Refactor into a reusable multi-board application architecture

## Deepening Summary

The original plan had the right direction but was still too loose in four
important ways:

1. It named the right layers, but did not assign a single source of truth for
   playbook metadata, surface metadata, executor capabilities, and routing
   decisions.
2. It proposed new abstractions that risked duplicating what
   `ApplicationTarget` already carries today.
3. It treated discovery as “board registration plus more logic,” when the
   current runtime shape is actually “provider-specific parsing under shared
   orchestration.”
4. It did not yet define the migration and compatibility rules needed to land
   new registries without breaking persisted plans, statuses, and in-flight
   Indeed drafts.

This deepened version keeps the same architectural intent but tightens the
design to better match repo conventions:

- small immutable dataclasses
- narrow `Protocol` seams
- simple registry maps with resolver functions
- playbook frontmatter as the authority for checkpoint and origin-allowlist
  metadata
- consumer-first rollout for new fields and states

The main architectural refinement is:

- keep `BoardAdapter` focused on provenance and intake normalization
- keep `ApplicationTarget` as the single resolved runtime record instead of
  introducing a second near-duplicate resolution object
- add a lightweight `SurfaceSpec` registry before introducing heavier surface
  behavior objects, and make it the authority for surface-owned metadata
- add a dedicated `DiscoveryProvider` contract rather than overloading boards,
  but keep the first contract narrow and typed
- upgrade executors from metadata-only records to typed runtime capability
  specs, but do so incrementally
- explicitly define the resolver pipeline and ownership rules so future boards
  can plug in without reintroducing split-brain
- preserve the compile-time invariant that the human, not the agent, clicks
  the final Submit button on every surface

## Execution Checklist

- [x] Revise the plan so the file/module layout, metadata ownership, and
  rollout rules are implementable
- [x] Extract surface and executor registries without breaking current Indeed
  and LinkedIn behavior
- [x] Introduce provider-shaped discovery registration without turning
  `job_hunt.discovery` into an invalid Python package/module collision
- [x] Persist enough routing and handoff context for deterministic resume and
  agent-readable manual-assist recovery
- [x] Add or update tests for registry routing, compatibility, and recovery
- [x] Mark the plan completed after implementation and verification finish

## Overview

This plan refactors the current Indeed-first pipeline into a reusable
multi-board architecture that can support LinkedIn next and additional job
sites after that without repeatedly rewriting discovery, routing, browser
execution, or application-state handling.

The strongest current code should remain intact: normalized leads, reusable
scoring, reusable draft preparation, playbook-driven application surfaces, and
board-specific parsing at the edges. The goal is not a rewrite. The goal is to
make “add a new website” a bounded task with explicit contracts instead of a
cross-cutting edit through `discovery.py`, `core.py`, and `application.py`.

This plan is intentionally broader than LinkedIn. LinkedIn is the immediate
driver, but the architecture should also support future boards such as
ZipRecruiter, Wellfound, Dice, and custom ATS portals without needing another
architecture pass first.

## Motivation

The repo can already do substantial end-to-end work for Indeed, and large
parts of that pipeline are reusable. The real risk is architectural drift:
adding a second or third board by bolting site-specific rules directly into
generic orchestration until the repo becomes a pile of board conditionals.

We want a design that preserves:

- normalized lead, draft, attempt, and reporting artifacts
- reusable candidate-profile scoring for Kashane Sakhakorn and future users
- reusable application preparation, answer-bank resolution, ATS checks, and
  generated assets
- reusable batch selection, lifecycle tracking, reconciliation, and reports
- explicit separation between discovery source, application surface, browser
  executor, and shared core domain logic

We also want explicit room for policy differences across boards. Indeed,
LinkedIn, Greenhouse, Workday, and future sites should not be forced into the
same runtime or policy posture just because they all represent “jobs.”

## Research Summary

### Repository patterns

The current codebase already contains the beginnings of the right abstraction
stack:

- `BoardAdapter`, `ApplicationTarget`, and `RemoteIngestionAdapter` in
  [src/job_hunt/boards/base.py](/Users/simons/job-hunt/src/job_hunt/boards/base.py:1)
- registry-based board resolution in
  [src/job_hunt/boards/registry.py](/Users/simons/job-hunt/src/job_hunt/boards/registry.py:1)
- reusable application planning and state handling in
  [src/job_hunt/application.py](/Users/simons/job-hunt/src/job_hunt/application.py:643)
- normalized lead extraction in
  [src/job_hunt/core.py](/Users/simons/job-hunt/src/job_hunt/core.py:1047)
- reusable scoring in
  [src/job_hunt/core.py](/Users/simons/job-hunt/src/job_hunt/core.py:1201)
- executor seam in
  [src/job_hunt/executors/base.py](/Users/simons/job-hunt/src/job_hunt/executors/base.py:1)
- tolerant playbook metadata loading in
  [src/job_hunt/playbooks.py](/Users/simons/job-hunt/src/job_hunt/playbooks.py:1)

### Institutional learnings

The strongest relevant prior decisions are:

- Separate `origin_board` from execution `surface`, so provenance and runtime
  behavior do not get conflated
  (see `docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md`)
- Reuse existing ATS playbooks when possible instead of creating one playbook
  per source board
  (see `docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md`)
- Ship tolerant consumers before strict producers when introducing new states,
  frontmatter, or schema members
  (see `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md`)
- Reconcile deepened plans with one clear authority per symbol, field, and
  artifact to avoid split-brain across prose and concrete deliverables
  (see `docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md`)

### Research decision

Proceeding without external research. The codebase already has strong local
patterns for the architectural question at hand, and this planning pass is
about internal structure and reuse more than framework uncertainty.

## Problem Statement

The repo currently has a reusable middle, but the edges are still only
partially modular:

- discovery source handling is not yet fully provider-pluggable
- remote ingestion capabilities are not registered and isolated uniformly
- browser execution is represented as metadata, but not yet as a typed runtime
  strategy boundary
- application surfaces are playbook-driven, but surface metadata still lives in
  multiple places
- some board-specific decisions still live in generic modules
- migration/compatibility rules for persisted artifacts are not yet explicit

That is acceptable for one or two boards, but it will degrade quickly if more
sites are added without tightening the contracts now.

## What We Are Building

We are building a layered multi-board architecture with four explicit seams:

1. **Board layer**
   Responsible for provenance, intake normalization, and conversion from
   board-specific metadata into normalized lead metadata.

2. **Surface layer**
   Responsible for application-flow semantics. A surface is where the actual
   form experience happens, such as `indeed_easy_apply`,
   `greenhouse_redirect`, or a future `linkedin_easy_apply`.

3. **Executor layer**
   Responsible for browser/runtime capabilities, checkpoints, screenshots,
   action primitives, retries, session handling, and recovery behavior.

4. **Core domain layer**
   Responsible for normalized artifacts: lead scoring, draft preparation,
   answer-bank resolution, ATS checks, generated assets, batch selection,
   attempts, lifecycle state, reporting, and analytics.

The rule is simple:

- boards know where jobs come from
- surfaces know how applications behave
- executors know what runtime capabilities exist
- the core domain knows how to prepare, track, and report work

## Why This Approach

### Recommended approach: layered board/surface/executor split

This approach preserves the current reusable pipeline while extracting the
remaining site-specific logic into explicit contracts.

**Pros**

- maximizes reuse of the strongest current code
- keeps future board additions bounded and testable
- reduces board conditionals in generic orchestration code
- allows policy differences per board and surface
- lets the team reuse ATS surfaces across many origin boards

**Cons**

- requires some up-front refactoring before adding more features
- introduces more named abstractions that need documentation and tests

**Best when**

- the repo is expected to support multiple websites over time
- discovery and browser behavior differ by site or surface
- policy/risk rules vary by board

### Rejected approach: keep adding board-specific conditionals

This would be faster for one more site, but it would make every additional
site harder and riskier than the last.

**Why rejected**

- grows cross-cutting board logic in generic modules
- hides policy differences inside runtime branches
- makes tests broader and less isolated
- does not scale cleanly to future sites

## Reuse Assessment

The current Indeed pipeline already provides substantial reusable value.

### High-reuse areas

- normalized lead storage and extraction
- candidate-profile scoring and ranking
- draft preparation and tiering
- answer-bank resolution
- ATS-check integration
- generated resume and cover-letter asset references
- status/attempt lifecycle
- stale-attempt reconciliation
- reporting and analytics
- batch candidate selection by score

### Medium-reuse areas

- board adapters and registry
- playbook metadata loading
- source filtering and target resolution
- executor metadata fields like `executor_backend`

### Low-reuse areas

- HTML/result parsing for each board or provider
- browser step logic for each hosted apply flow
- anti-bot/session quirks per site

## Single Source Of Truth

The deepened design assigns one authority per concern:

- **Board registry** owns provenance matching and board adapter lookup.
- **Discovery provider registry** owns provider-specific fetch/parse contracts.
- **Surface registry** owns surface metadata such as `playbook_path`,
  `default_executor`, `surface_policy` defaults, `handoff_kind`, and the
  `batch_eligible(surface, target)` predicate.
- **Executor registry** owns runtime capability declarations and runtime
  selection metadata.
- **Playbook frontmatter** remains authoritative for checkpoint sequence and
  origin allowlist details.
- **Core domain artifacts** remain authoritative for normalized plan/status/
  attempt/reporting records.

This means we should not declare `playbook_path`, `executor_backend`,
`surface_policy`, `handoff_kind`, or `batch_eligible` in multiple Python
authorities once the surface registry lands. The acceptance criteria below make
that explicit.

## Resolver Pipeline

The architecture should use one explicit resolver pipeline:

```text
lead
  -> board adapter
  -> board resolution
  -> surface resolver / surface spec
  -> policy evaluator
  -> executor selector
  -> prepare_application
  -> apply_posting handoff
```

Each stage has a narrow scope:

- **Board adapter**
  Normalizes board provenance and board-specific hints.
- **Board resolution**
  Produces source-side facts without deciding runtime behavior beyond what the
  board can authoritatively know.
- **Surface resolver**
  Determines execution surface from normalized facts such as URL host,
  `apply_type`, and redirect chain.
- **Policy evaluator**
  Decides whether the chosen surface is automatable, manual-assist-only,
  skipped, or needs escalation under current runtime policy.
- **Executor selector**
  Chooses a runtime backend based on the selected surface and executor
  capability requirements.

This keeps `application.py` orchestration-oriented instead of letting it remain
the real source of business logic.

## Architecture Decisions

### 1. Keep `origin_board` and `surface` as separate first-class concepts

This is already the right direction and must remain the foundation.

- `origin_board` answers: where did the lead come from?
- `surface` answers: what application experience do we execute?

This avoids coupling a source board like LinkedIn to a hosted form surface it
may or may not own.

### 2. Keep `BoardAdapter` as the provenance/intake contract

`BoardAdapter` should remain responsible for:

- board identity and matching
- manual/local intake normalization
- creating a lightweight board-side resolution from a normalized lead

`RemoteIngestionAdapter` should remain optional, not default. That keeps
future boards from accidentally becoming fetch-enabled just because the base
interface made it convenient.

### 3. Keep `ApplicationTarget` as the single resolved runtime record

The original deepening added a `BoardResolution` object between board and
surface, but that recreates most of what `ApplicationTarget` already carries
today and would force the rollout to reconcile two near-duplicate contracts.

Instead:

- keep `ApplicationTarget` as the resolved runtime record
- let board adapters continue to contribute board-side facts like
  `origin_board`, `apply_host`, and `redirect_chain`
- move surface-owned metadata behind registry lookups keyed by `surface`
- extend `ApplicationTarget` only when a new field is genuinely needed by
  downstream consumers

This keeps one runtime spine while still separating provenance from surface
semantics.

### 4. Start the surface layer as `SurfaceSpec`, not a heavy handler object

The repo currently favors small immutable dataclasses and simple registries.
To stay aligned with that pattern, the first surface layer should be
metadata-first:

```python
@dataclass(frozen=True)
class SurfaceSpec:
    surface: str
    playbook_path: str
    default_executor: str
    default_surface_policy: str
    handoff_kind: str
```

Resolver functions can sit alongside it:

```python
def get_surface_spec(surface: str) -> SurfaceSpec: ...
def batch_eligible(surface: str, target: ApplicationTarget) -> bool: ...
def cover_letter_policy(surface: str) -> dict: ...
```

Only introduce richer surface behavior objects later if surface-specific logic
grows beyond metadata and a few pure functions.

Manual-assist and escalation outcomes still need a first-class durable
contract. Even while `SurfaceSpec` stays lightweight, the plan/status artifacts
must carry:

- the handoff kind
- the current human-handoff checkpoint
- operator checklist / review items when required
- resume instructions after restart

Those runtime artifacts, not hidden policy branches, are what keep assisted
flows agent-readable and recoverable.

### 5. Add a dedicated `DiscoveryProvider` contract

Discovery providers are not the same thing as boards. `careers` is a crawl
mode, `indeed_search` is a search provider, and future providers may not map
cleanly to a board name.

Use a separate contract:

```python
@dataclass(frozen=True)
class DiscoveryPage:
    entries: tuple[ListingEntry, ...]
    truncated: bool
    next_cursor: str | None = None


class DiscoveryProvider(Protocol):
    name: str

    def list_entries(
        self,
        company: str,
        *,
        cursor: str | None = None,
    ) -> DiscoveryPage: ...
```

The first version should stay narrow and typed. Cursor tokens, truncation, and
provider pagination need to be explicit in the return value rather than hidden
behind `*args`/`**kwargs` or side channels. The architectural point is still
that discovery remains provider-shaped, while provenance remains board-shaped.

### 6. Upgrade executors incrementally with typed capability specs

The current executor seam in
[src/job_hunt/executors/base.py](/Users/simons/job-hunt/src/job_hunt/executors/base.py:1)
is metadata only. That is the right starting point, but the plan needs a typed
capability model before a second real backend lands.

Start with:

```python
@dataclass(frozen=True)
class ExecutorCapabilities:
    browser_automation: bool
    file_upload: bool
    tab_management: bool
    checkpoint_resume: bool
    auth_session_reuse: bool
    screenshot_capture: bool
    dom_read: bool
```

Then extend the executor record:

```python
@dataclass(frozen=True)
class ExecutorSpec:
    name: str
    capabilities: ExecutorCapabilities
    notes: str = ""
```

Do not jump immediately to large strategy objects unless real backend-specific
runtime behavior is ready to move out of `application.py`.

The no-auto-submit invariant remains compile-time and non-negotiable:

- executors may automate navigation, field entry, and final-review reachability
- executors may never click the final Submit button
- surface or runtime policy may tighten review depth, but may not loosen the
  human-submit boundary

### 7. Keep the normalized domain model board-agnostic

Do not fork lead, draft, or attempt schemas per board. Add board-specific
fields only when they represent real domain concepts that other consumers can
understand.

Examples of acceptable board-specific fields:

- `origin_board`
- `redirect_chain`
- `surface_policy`
- `handoff_kind`
- `executor_backend`
- stable board posting identifiers when useful for dedupe or confirmation

Examples of fields to avoid in generic artifacts:

- raw DOM selectors
- executor-only temporary step state
- ephemeral browser UI details with no downstream value

### 8. Prefer composition over inheritance

Boards, discovery providers, surfaces, and executors should each own their
side of behavior. The system should compose them through registries, not deep
class hierarchies.

That keeps the codebase closer to state-of-the-art maintainability:

- simple protocols
- explicit dataclasses
- clear ownership boundaries
- testable composition
- small modules with narrow responsibility

## Normalized Lead Contract For Application Prep

Before `prepare_application()` may run, the lead should satisfy a minimum
normalized contract:

Required:

- `lead_id`
- `company`
- `title`
- `raw_description`
- `normalized_requirements`
- enough URL/application metadata to resolve an execution surface without
  scraping at prepare time

Preferred but optional:

- `origin_board`
- `apply_type`
- `redirect_chain`
- provider-specific stable identifiers

Design rule:

- `prepare_application()` should not need to understand raw board parsing
  shapes.
- Board/provider code must normalize enough data ahead of time that
  application prep stays generic.

## Surface Resolution Decision Table

The system should document and test surface routing with explicit examples:

| origin_board | apply_type | final host / hint | resolved surface | surface_policy | batch_eligible | handoff_kind |
|---|---|---|---|---|---|---|
| `indeed` | `direct` | `indeed.com` | `indeed_easy_apply` | automated to final review | yes | automation_playbook |
| `indeed` | `external` | ATS host | `indeed_external_redirect` or concrete ATS redirect surface | automated to final review | no for Indeed batch until supported | automation_playbook |
| `linkedin` | `external` | `boards.greenhouse.io` in redirect chain | `greenhouse_redirect` | automated to final review | yes | automation_playbook |
| `linkedin` | `direct` | `linkedin.com` | `linkedin_easy_apply_assisted` in current repo posture | manual-assist or future automated-final-review policy | no today | manual_assist |
| future board | unknown | unsupported host | surface unresolved / manual-assist fallback | escalated | no | manual_assist |

The exact final behavior for LinkedIn-hosted flows may change later, but the
table format should remain the routing authority.

All rows in this table still terminate at human final submit. Language such as
`automated to final review` means the agent may prepare the application up to
the review screen, not that it may submit on the user's behalf.

## Executor Selection And Fallback Policy

Executor selection should be deterministic:

1. surface default executor is looked up from `SurfaceSpec`
2. runtime override may replace it only if the override satisfies required
   capabilities
3. capability mismatch yields a structured paused/escalated result, not a
   silent branch or generic failure

Fallback outcomes should be explicit:

- `execute`
- `manual_assist`
- `skip`
- `escalate`

This is especially important once multiple browser runtimes exist.

## Resume And Recovery Contract

The plan should distinguish durable domain state from runtime scratch state.

Persist in normalized artifacts:

- chosen `surface`
- chosen `executor_backend`
- resolver inputs needed to explain the choice, such as `posting_url`,
  `apply_type`, `apply_host`, and `redirect_chain`
- registry/resolver schema version
- lifecycle state
- domain-level checkpoints
- attempt status
- user-facing remediation or escalation reason
- human-handoff checkpoint when present

Do not persist in normalized artifacts:

- transient DOM locator state
- browser-internal references
- executor-private retry internals

The goal is resumability without letting normalized artifacts become executor
implementation logs.

Recommended artifact shape:

```python
"routing_snapshot": {
    "schema_version": 1,
    "posting_url": posting_url,
    "apply_type": lead.get("apply_type"),
    "apply_host": target.apply_host,
    "redirect_chain": target.redirect_chain,
    "surface": target.surface,
    "executor_backend": target.executor_backend,
    "resolver_version": 1,
}
```

If a future resume path intentionally re-resolves under newer rules, that
should be recorded as a new audited transition rather than silently replacing
the original decision context.

## Error Ownership Matrix

Error ownership should be explicit:

- **Discovery provider errors**
  Fetch, robots, anti-bot, cursor, parsing, provider-specific listing failures
- **Board errors**
  Provenance normalization or source-side metadata contradictions
- **Surface errors**
  Unsupported surface, unresolved surface, surface metadata mismatch
- **Executor errors**
  capability mismatch, session failure, login wall, MFA, CAPTCHA, browser
  runtime issues
- **Core domain errors**
  invalid plan, draft collisions, ATS/preflight issues, missing required
  normalized inputs

Each new error should also define whether the outcome is:

- retry
- pause for human
- skip
- hard fail

## Dependency Rules

The deepened architecture should encode import-direction rules:

- `core.py` should not grow new imports of boards, surfaces, or executors
- `application.py` may depend on registries, not concrete per-board/per-surface
  modules where avoidable
- board adapters may depend on board-local helpers and shared types
- discovery providers may depend on provider-local parsing/fetch logic and
  shared orchestration contracts
- surface registry/helpers may depend on playbook helpers
- executors may consume normalized plans and handoff bundles, but not board
  adapters

This addresses existing layering creep such as the board-aware imports in
[src/job_hunt/core.py](/Users/simons/job-hunt/src/job_hunt/core.py:1047).

## Proposed Module Structure

### Keep

- `src/job_hunt/boards/base.py`
- `src/job_hunt/boards/registry.py`
- `src/job_hunt/application.py`
- `src/job_hunt/playbooks.py`
- `src/job_hunt/executors/base.py`
- `src/job_hunt/discovery.py`
- `src/job_hunt/indeed_discovery.py`

### Add

- `src/job_hunt/discovery_providers/base.py`
- `src/job_hunt/discovery_providers/registry.py`
- `src/job_hunt/surfaces/base.py`
- `src/job_hunt/surfaces/registry.py`
- `src/job_hunt/executors/registry.py`

### Add later only if behavior justifies it

- `src/job_hunt/linkedin_discovery.py`
- per-surface modules beyond `registry.py` and `base.py`

### Explicitly avoid for now

- a framework of empty per-board/per-surface handler modules
- a single `ats_redirect.py` catch-all that collapses distinct redirect
  surfaces back together

## Phase Plan

### Phase 0: Authority consolidation

Goal: remove duplicate authorities before adding new abstractions.

Deliverables:

- choose one authoritative mapping for `surface -> playbook path`
- choose one authoritative owner for `batch_eligible`
- preserve playbook frontmatter as authority for checkpoint and origin allowlist
- document which metadata lives in `ApplicationTarget` today and what will move
  to `SurfaceSpec`

Acceptance criteria:

- surface/playbook metadata is not declared in multiple Python authorities
- `batch_eligible` is derived from exactly one surface-owned predicate
- no behavior change for current Indeed/LinkedIn routing

### Phase 1: Stabilize surface and executor seams

Goal: finish the abstractions already hinted at by the current code, using
Indeed as the reference vertical slice.

Deliverables:

- add `SurfaceSpec` base contract and registry
- add executor registry and typed capability records
- move surface-specific helpers behind the surface registry
- keep `ApplicationTarget` as the single runtime resolution record
- keep current Indeed and ATS redirect behavior unchanged

Acceptance criteria:

- current Indeed tests still pass with no behavioral regression
- `prepare_application()` resolves surfaces through the new registry
- `apply_posting()` emits executor and surface metadata through registry-backed
  calls instead of ad hoc conditionals
- current Indeed drafts remain readable/resumable
- manual-assist surfaces expose durable checklist/review/resume data through
  plan/status artifacts or handoff bundles

### Phase 2: Make discovery pluggable by provider

Goal: make discovery provider-shaped without regressing the shared
orchestration in `discovery.py`.

Deliverables:

- introduce `DiscoveryProvider` registry instead of one hard-coded source
  switch
- keep `greenhouse`, `lever`, `careers`, and `indeed_search` behavior intact
- retain `discovery.py` as shared orchestration
- place provider implementations in `src/job_hunt/discovery_providers/` to
  avoid a `job_hunt.discovery` module/package collision
- treat `indeed_discovery.py` as the model for future provider-specific
  extraction

Acceptance criteria:

- `discover_jobs()` can enumerate enabled providers from a registry
- adding a new provider no longer requires editing one giant dispatch branch
- provider-specific parsing/fetch logic is separated from shared rate limiting,
  dedupe, cursor, and scoring orchestration
- the public `job_hunt.discovery` import surface remains valid throughout the
  rollout

### Phase 3: Refactor batch eligibility and policy evaluation

Goal: remove board-name branching from generic apply orchestration.

Deliverables:

- central policy evaluator between surface resolution and executor selection
- surface/policy-driven batch eligibility predicates
- compatibility handling for existing persisted artifacts

Acceptance criteria:

- `apply_batch()` no longer needs board-name branches for batch eligibility
- executor mismatch produces a structured paused/escalated result with audit
  metadata
- legacy plans/status files still load under tolerant consumers

### Phase 4: Land LinkedIn on top of the improved seams

Goal: add LinkedIn support using the new architecture instead of bending the
old one.

Deliverables:

- LinkedIn discovery provider and/or intake provider, depending on policy scope
- LinkedIn surface metadata and routing through shared registries
- LinkedIn executor integration path
- policy, checkpoints, and status lifecycle integrated through existing
  normalized artifacts

Acceptance criteria:

- LinkedIn support reuses the shared middle and downstream pipeline
- LinkedIn-specific logic lives mostly in provider, board, surface, and
  executor modules
- `application.py` remains broadly board-agnostic

## Legacy Artifact Rollout Plan

This refactor affects persisted artifacts, so rollout must stay
consumer-first.

Rules:

1. Add tolerant readers first.
2. Backfill missing values at read time before introducing strict migrations.
3. Only after readers are tolerant should new producers emit registry-driven
   fields or stricter semantics.
4. `check-integrity` should become the eventual strict-mode promotion point,
   not the first landing point.
5. New routing or handoff fields must remain optional for legacy drafts until
   compatibility checks prove all producers and consumers are upgraded.

This follows the repo’s documented pattern in
`ship-tolerant-consumers-before-strict-producers.md`.

## Observability And Audit

Attempt and handoff artifacts should become slightly richer so multi-board
routing stays debuggable.

Recommended additions:

- selected board adapter/provider/surface/executor names
- executor capability snapshot
- fallback or escalation reason
- pause point / human-handoff checkpoint
- evidence that final-review screen was reached when applicable
- routing snapshot with resolver version and normalized routing inputs

The goal is enough auditability to debug `board -> surface -> executor`
decisions without polluting normalized artifacts with raw runtime internals.

## New Board Onboarding Checklist

Every new board or provider should answer:

1. What is the provenance token and normalized source name?
2. Does it need a discovery provider, a board adapter, or both?
3. Does it support remote ingestion or only local/manual intake?
4. Which execution surfaces can it resolve to?
5. What policy boundary applies at the origin host?
6. Which executor capabilities are required?
7. What fixtures and parser tests are needed?
8. What batch-eligibility rule applies?
9. What error codes or outcomes are new?
10. Does adding it require tolerant-consumer schema/state updates first?

## SpecFlow Analysis

### Happy paths

- discover jobs from a configured provider
- normalize leads into the shared lead shape
- score leads against Kashane’s profile
- select top-ranked leads
- prepare drafts and assets
- hand off to a browser executor
- stop at human review/final submit
- record attempts and outcomes

### Edge cases the plan must cover

- a board can discover jobs but not support remote ingestion
- discovery from remote source vs local/manual lead ingestion
- origin board equals surface host vs origin board differs from surface host
- fully automatable surface vs manual-assist-only surface vs future
  partial-automation surface
- executor available but lacking capabilities vs executor unavailable entirely
- fresh session vs expired session vs MFA gate vs anti-bot gate
- lead prepared under one architecture version and resumed under another
- surface resolved at prepare time may need a controlled runtime correction
  with audit trail

### Design guardrails from this analysis

- every provider must declare its discovery contract
- every board must declare whether it supports remote ingestion
- every surface must declare its metadata and batch-eligibility policy through
  one authority
- every executor must declare typed capabilities instead of ad hoc booleans
- lifecycle/schema changes must follow tolerant-consumer rollout
- “ready for human final submit” must be a real, consistent handoff contract

## Risks

### Over-abstracting too early

Mitigation:

- keep protocols and dataclasses small
- extract only seams already proven by current code
- do not invent a framework for hypothetical boards

### Duplicating authorities across board/surface/playbook layers

Mitigation:

- enforce the single-source-of-truth rules above
- preserve playbook frontmatter as authority for checkpoint and allowlist
  details
- keep surface metadata in one Python authority only

### Letting policy rules leak into generic orchestration

Mitigation:

- keep board, surface, and executor policy inputs explicit
- centralize policy evaluation rather than scattering `if board == ...`

### Schema/state churn

Mitigation:

- apply tolerant-consumer-first rollout for new enums and frontmatter
- extend tests before producers write new values

### Empty abstraction framework

Mitigation:

- do not create many per-board/per-surface modules until they own distinct,
  testable behavior

## Testing Strategy

- keep targeted unit tests for each board adapter
- add focused tests for discovery providers
- add surface registry/resolution tests
- add executor capability tests and handoff-shape tests
- preserve existing end-to-end tests for Indeed
- add regression tests asserting new boards/providers do not require edits to
  generic orchestration switches
- add compatibility tests for old and new `plan.json` / `status.json` shapes
- add tests that existing recovery/mutation interfaces (`apply_status`,
  `checkpoint_update`, `refresh_application`, `mark_applied_externally`,
  `withdraw_application`, `reopen_application`) still work after the registry
  refactor

## File Plan

### New files

- `src/job_hunt/discovery/base.py`
- `src/job_hunt/discovery/registry.py`
- `src/job_hunt/surfaces/base.py`
- `src/job_hunt/surfaces/registry.py`
- `src/job_hunt/executors/registry.py`
- `tests/test_discovery_registry.py`
- `tests/test_surfaces.py`
- `tests/test_executors.py`
- `tests/test_plan_status_compat.py`

### Existing files to update

- `src/job_hunt/application.py`
- `src/job_hunt/discovery.py`
- `src/job_hunt/discovery_providers/base.py`
- `src/job_hunt/discovery_providers/registry.py`
- `src/job_hunt/boards/base.py`
- `src/job_hunt/boards/registry.py`
- `src/job_hunt/core.py`
- `src/job_hunt/executors/base.py`
- `src/job_hunt/executors/claude_chrome.py`
- `src/job_hunt/executors/codex_browser.py`
- `src/job_hunt/playbooks.py`
- `README.md`

## Acceptance Criteria

- [ ] The repo has explicit board, discovery-provider, surface, and executor registries.
- [ ] Current Indeed behavior is preserved through the new registries.
- [ ] `prepare_application()` consumes normalized lead data plus registries rather than board-specific raw parsing shapes.
- [ ] `apply_batch()` no longer contains board-name branches for batch eligibility.
- [ ] Executor capabilities are typed and test-covered.
- [ ] Playbook frontmatter remains authoritative for checkpoint sequence and origin allowlist data.
- [ ] No surface metadata is declared in more than one authoritative place; `playbook_path`, `executor_backend`, `surface_policy`, `handoff_kind`, and `batch_eligible` are each sourced from exactly one registry or artifact.
- [ ] Legacy Indeed drafts remain readable and resumable after registry introduction.
- [ ] LinkedIn can be added using the new seams instead of a generic-module patch.
- [ ] Future boards/providers can be introduced by implementing contracts rather than editing multiple unrelated orchestration branches.
- [ ] Routing and handoff artifacts store enough versioned context for deterministic resume or explicit audited re-resolution.
- [ ] Manual-assist and escalation outcomes remain agent-readable and recoverable.
- [ ] The human-submit invariant is explicitly preserved across all surfaces and executors.

## Success Metrics

- adding a new board or discovery provider requires touching fewer generic
  modules than today
- new board/provider behavior is mostly covered by focused provider/board/
  surface/executor tests
- `application.py` and `discovery.py` shrink in board/provider-specific
  branching over time
- future board plans can point to this architecture instead of re-deriving the
  layering

## Open Questions

- Should surface resolution remain a pure function over `BoardResolution`, or
  should any surface-specific runtime hints be permitted at prepare time?
- Should executor selection support runtime overrides immediately, or should
  that land after one backend is fully migrated onto the registry model?
- Which capability vocabulary is sufficient for the first typed
  `ExecutorCapabilities` record without overfitting to one future board?

## Sources

- [src/job_hunt/boards/base.py](/Users/simons/job-hunt/src/job_hunt/boards/base.py:1)
- [src/job_hunt/boards/registry.py](/Users/simons/job-hunt/src/job_hunt/boards/registry.py:1)
- [src/job_hunt/application.py](/Users/simons/job-hunt/src/job_hunt/application.py:643)
- [src/job_hunt/core.py](/Users/simons/job-hunt/src/job_hunt/core.py:1047)
- [src/job_hunt/core.py](/Users/simons/job-hunt/src/job_hunt/core.py:1201)
- [src/job_hunt/discovery.py](/Users/simons/job-hunt/src/job_hunt/discovery.py:93)
- [src/job_hunt/indeed_discovery.py](/Users/simons/job-hunt/src/job_hunt/indeed_discovery.py:1)
- [src/job_hunt/executors/base.py](/Users/simons/job-hunt/src/job_hunt/executors/base.py:1)
- [src/job_hunt/playbooks.py](/Users/simons/job-hunt/src/job_hunt/playbooks.py:1)
- [docs/plans/2026-04-19-001-feat-linkedin-and-board-adapters-plan.md](/Users/simons/job-hunt/docs/plans/2026-04-19-001-feat-linkedin-and-board-adapters-plan.md:1)
- [docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/harden-board-integration-plans-with-origin-surface-separation.md:1)
- [docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md](/Users/simons/job-hunt/docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md:1)
- [docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md:1)
- [docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md](/Users/simons/job-hunt/docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md:1)
