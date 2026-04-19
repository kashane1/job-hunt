---
title: "feat: Add LinkedIn support via reusable board adapters and assisted/manual LinkedIn flows"
type: feat
status: active
date: 2026-04-19
deepened: 2026-04-19
---

# feat: Add LinkedIn Support via Reusable Board Adapters and Assisted/Manual LinkedIn Flows

## Deepening Summary

The original draft was directionally right about separating board-specific
logic from the existing Indeed pipeline, but it was still too loose in three
important ways:

1. It weakened the repo's current LinkedIn hard-fail posture without replacing
   it with an equally strong invariant.
2. It treated LinkedIn-hosted application flows as a defaultable policy choice
   instead of a hard no-automation boundary.
3. It did not define the state model required for a durable manual-assist flow.

This deepened plan tightens all three. The core recommendation is:

- build a reusable **board adapter** architecture
- keep the existing **execution-surface** playbooks and status machinery
- support LinkedIn as an **origin board**
- support LinkedIn-origin jobs that redirect to Greenhouse/Lever/Workday/Ashby
- treat LinkedIn-hosted application flows as **manual-assist only**
- keep `linkedin.com` outside fetch-based ingestion/discovery in v1

That preserves the repo's "trustworthy job-search system" mission and still
lays the right foundation for many future boards.

## External Constraints

### LinkedIn policy boundary

As of April 19, 2026, LinkedIn Help states that it does not allow the use of
third-party software or browser extensions that scrape, modify the appearance
of, or automate activity on LinkedIn's website. LinkedIn also documents daily
limits, speed limits, and inauthentic-behavior limits for Easy Apply.

Official sources reviewed:

- [Automated activity on LinkedIn](https://www.linkedin.com/help/linkedin/answer/a1341543)
- [Apply to jobs directly on LinkedIn](https://www.linkedin.com/help/linkedin/answer/a512348)
- [Easy Apply limits](https://www.linkedin.com/help/linkedin/answer/a8068422)
- [ATS integrations in Recruiter](https://www.linkedin.com/help/linkedin/answer/a496957)
- [Apply Connect prerequisites and feature availability](https://www.linkedin.com/help/linkedin/answer/a513223)

This is stricter than the repo's current Indeed posture. The repo therefore
needs a **harder** policy boundary for LinkedIn than it has for Indeed.

### Anthropic/Chrome tooling context

Anthropic's release notes describe Claude in Chrome support for browser
testing, workflow recording, and Claude Code integration. That is enough to
justify designing a shared backend/handoff seam, but not enough to justify
claiming runtime parity in this repo before a human validates it.

Source reviewed:

- [Claude release notes](https://support.claude.com/en/articles/12138966-release-notes)

## Hard Invariants

These are not defaults. They are the deepened plan's required boundaries.

### Compile-time invariants

1. `linkedin.com` remains **outside** `config/domain-allowlist.yaml` in v1.
2. `ingest-url` and `discover-jobs` continue to hard-fail LinkedIn URLs.
3. No executor or automated playbook may navigate, inspect, click, type,
   upload, or otherwise automate `linkedin.com` application surfaces.
4. `apply-batch --source linkedin` excludes LinkedIn-hosted Easy Apply and
   only processes LinkedIn-origin jobs whose execution surface resolves to a
   supported external ATS.

### Runtime-enforced invariants

1. LinkedIn sign-in, password entry, MFA, CAPTCHA, identity verification,
   profile-completion gates, and account restrictions are always manual-only
   blockers.
2. LinkedIn-origin artifact data must be canonicalized and redacted before
   persistence.
3. Existing human-submit and account-creation boundaries remain in force.

### Documentation-only items

1. Operator workflow guidance for LinkedIn manual-assist.
2. Claude Chrome parity notes, explicitly marked unverified until tested by a
   human.

## Decision

### Supported v1 scope

Ship LinkedIn support in three lanes:

1. **LinkedIn-origin leads via manual/local intake**
   - The user provides a local markdown/JSON intake file.
   - The repo continues to use `extract-lead --input <file>` as the canonical
     ingestion path.
   - No fetch-based LinkedIn scraping or watchlist polling ships in v1.

2. **LinkedIn-origin jobs that redirect to supported external ATS hosts**
   - The job's origin board is LinkedIn.
   - The manual/local intake artifact must include a deterministic execution
     target for the job:
     - either `application_url` / `canonical_url` pointing directly at the
       external ATS host, or
     - a sanitized `redirect_chain` whose final URL is the external ATS host.
   - If the intake artifact does not contain a deterministic final execution
     URL, the draft is treated as `linkedin_easy_apply_assisted` and is not
     batch-eligible.
   - The execution surface is one of the existing ATS playbooks:
     `greenhouse_redirect`, `lever_redirect`, `workday_redirect`,
     `ashby_redirect`.
   - These jobs are eligible for automated execution under the same current
     repo rules as other ATS-hosted flows.

3. **LinkedIn-hosted Easy Apply / inline apply**
   - The repo prepares answers, review summaries, provenance, and assets.
   - The repo emits a structured manual-assist bundle.
   - The human performs all activity on `linkedin.com`.
   - The repo records the lifecycle and outcome afterward.

### Explicit non-goals for v1

1. No fetch-based LinkedIn ingestion.
2. No LinkedIn watchlist/discovery polling.
3. No executor automation on `linkedin.com`.
4. No live automated browser tests against LinkedIn-hosted flows.

## Architecture

### Core architectural adjustment: separate board from execution surface

The repo should stop overloading `surface` to mean both "where this job came
from" and "how we execute it."

The deepened model is:

- `origin_board`: where the lead came from
- `surface`: which execution surface actually handles the apply flow

Examples:

| Origin board | Execution surface | Notes |
|---|---|---|
| `indeed` | `indeed_easy_apply` | existing behavior |
| `indeed` | `greenhouse_redirect` | existing redirect reuse |
| `linkedin` | `greenhouse_redirect` | new LinkedIn-origin redirect reuse |
| `linkedin` | `linkedin_easy_apply_assisted` | manual-assist only |

This is the smallest safe refactor because it lets us reuse the current ATS
playbooks without creating redundant LinkedIn-specific redirect playbooks.

### Reuse vs extract

Keep centralized and shared:

- `prepare_application()` tiering, answer-bank resolution, ATS-check wiring,
  and artifact writes in [src/job_hunt/application.py](/Users/simons/job-hunt/src/job_hunt/application.py:481)
- `record_attempt()`, `checkpoint_update()`, stale-attempt reconciliation, and
  lifecycle merging in [src/job_hunt/application.py](/Users/simons/job-hunt/src/job_hunt/application.py:813)
- current ATS playbooks and the generic router in [playbooks/application](/Users/simons/job-hunt/playbooks/application)
- tolerant playbook metadata loaders in [src/job_hunt/playbooks.py](/Users/simons/job-hunt/src/job_hunt/playbooks.py:19)
- lead extraction via [extract_lead()](/Users/simons/job-hunt/src/job_hunt/core.py:1037)

Extract into a board layer:

- URL/lead classification currently embedded in `detect_surface()`
- board-specific correlation-key enrichment
- board-specific manual-intake metadata normalization
- fetch-capable board-specific ingestion metadata extraction
- board-specific batch eligibility rules

### Minimal board adapter contract

Create:

- `src/job_hunt/boards/base.py`
- `src/job_hunt/boards/registry.py`
- `src/job_hunt/boards/indeed.py`
- `src/job_hunt/boards/linkedin.py`

Split the adapter contract so fetch-based ingestion is not the default.

Base contract:

```python
class BoardAdapter(Protocol):
    name: str

    def matches_lead_or_url(self, lead: dict | None, url: str) -> bool: ...

    def resolve_application_target(self, lead: dict) -> dict:
        """
        Returns:
          {
            "origin_board": str,
            "surface": str,
            "playbook_path": str,
            "surface_policy": str,
            "correlation_keys_patch": dict,
            "batch_eligible": bool,
          }
        """
    
    def normalize_manual_intake(self, metadata: dict) -> dict: ...
```

Optional fetch-capable extension:

```python
class RemoteIngestionAdapter(BoardAdapter, Protocol):
    def ingest_remote_metadata(
        self,
        url: str,
        html_text: str | None = None,
    ) -> dict: ...
```

Compatibility wrapper:

- `application.detect_surface()` remains as a thin delegate during migration
- `playbook_for_surface()` remains as a thin delegate during migration

This keeps existing tests and callers stable while the registry lands.

LinkedIn-specific rule:

- `src/job_hunt/boards/linkedin.py` implements `BoardAdapter`
- it does **not** implement `RemoteIngestionAdapter` in v1
- it may normalize local/manual intake metadata only

This preserves the repo's hard boundary against fetch-based LinkedIn
inspection while still allowing LinkedIn-origin work to enter the system.

### Executor backends: additive seam only in v1

The first draft over-scoped executor abstraction. The deepened plan trims it.

Phase 1 should **not** try to make current markdown playbooks fully
backend-neutral. Existing playbooks still hard-code Claude-in-Chrome actions,
and that is acceptable for now.

Instead, add an additive handoff seam:

- `handoff_kind`: `automation_playbook` or `manual_assist`
- `executor_backend`: `claude_chrome`, `codex_browser`, or `none`

Only later should the repo attempt to convert playbooks into backend-neutral
execution specs.

Planned files:

- `src/job_hunt/executors/base.py`
- `src/job_hunt/executors/codex_browser.py`
- `src/job_hunt/executors/claude_chrome.py`

But in v1 these can be stubs/documentation seams rather than runtime-critical
dependencies.

## LinkedIn-Specific Design

### Intake model

LinkedIn support begins with manual/local intake, not network fetches.

Recommended operator path:

1. Save a local markdown or JSON representation of the LinkedIn job.
2. If the job is expected to reuse an external ATS playbook, capture the
   external apply target in the local artifact:
   - direct `application_url` / `canonical_url` to the ATS host, or
   - sanitized `redirect_chain` ending at the ATS host.
3. If no deterministic external target is captured, the job is treated as a
   LinkedIn-hosted manual-assist flow.
4. Run `python3 scripts/job_hunt.py extract-lead --input <file>`.
5. Continue through the normal scoring and `prepare-application` path.

This preserves the repo's existing hard-fail boundary for LinkedIn URLs while
still making LinkedIn-origin jobs usable inside the system.

### Surface policies

Use explicit, durable values:

- `browser_automated_human_submit`
- `manual_assist`
- `automation_forbidden_on_origin`

Recommended mapping:

| Surface | Policy |
|---|---|
| `indeed_easy_apply` | `browser_automated_human_submit` |
| `greenhouse_redirect` | `browser_automated_human_submit` |
| `lever_redirect` | `browser_automated_human_submit` |
| `workday_redirect` | `browser_automated_human_submit` |
| `ashby_redirect` | `browser_automated_human_submit` |
| `linkedin_easy_apply_assisted` | `automation_forbidden_on_origin` |

Important: for LinkedIn-hosted flows this is not a runtime-tunable preference.
It is a repo invariant.

### LinkedIn-hosted manual-assist flow

Planned playbook:

- `playbooks/application/linkedin-easy-apply-assisted.md`

It is not an automation playbook. It is an operator-assist playbook.

Flow:

1. Human opens the LinkedIn job page manually.
2. `prepare-application` generates a normal plan with LinkedIn origin
   metadata, answer-bank answers, assets, and review notes.
3. `apply-posting` emits a `manual_assist` bundle containing:
   - `field_summary`
   - `review_items`
   - `resume_path`
   - `cover_letter_path`
   - `operator_checklist`
   - `surface_policy=automation_forbidden_on_origin`
4. Human performs the actual LinkedIn interaction.
5. Human or agent records one of the supported outcomes.

### LinkedIn-origin redirect reuse

When a LinkedIn-origin job resolves to a supported external ATS:

1. Preserve `origin_board=linkedin`.
2. Resolve `surface` to the actual ATS host playbook.
3. Preserve a sanitized `redirect_chain`.
4. Reuse the existing ATS playbooks.

No LinkedIn-specific redirect playbooks are needed in v1.

## State Model Additions

The current attempt/status model has no durable "waiting on a human after we
prepared a manual-assist bundle" state. That must change for LinkedIn.

### Attempt statuses

Add:

- `paused_manual_assist`

Optional future split:

- `handoff_completed_pending_outcome`

### Lifecycle states

Add:

- `awaiting_human_action`

This is distinct from:

- `drafted` - nothing has started yet
- `applying` - automated execution is in progress

### Mapping rules

Recommended additions to `lead_state_from_attempt()`:

- `paused_manual_assist` -> `awaiting_human_action`
- `paused_human_abort` from a manual-assist flow should preserve structured
  abort context rather than silently behaving like a never-touched draft

### Event/reporting additions

If the repo does not log a durable event for manual handoffs, dashboards will
under-report LinkedIn work.

Add event/reporting support for:

- `manual_assist_deferred`
- `human_aborted`
- `policy_blocked`

## Checkpoints and Errors

### LinkedIn-hosted checkpoints

Proposed `checkpoint_sequence` for `linkedin_easy_apply_assisted`:

- `preflight_done`
- `assist_bundle_ready`
- `human_form_in_progress`
- `human_ready_to_submit`
- `human_submit_recorded`
- `confirmation_captured`

### Redirect checkpoints

Redirect handling must be explicit, not hand-wavy.

For LinkedIn-origin jobs that land on a supported ATS:

- `preflight_done`
- `listing_opened`
- `redirect_detected`
- `redirect_verified`
- `ats_handoff_started`
- `ats_handoff_completed`

### Missing error codes to add

- `manual_only_surface`
- `linkedin_apply_limit_reached`
- `platform_account_restricted`
- `redirect_chain_unresolved`
- `redirect_loop_detected`
- `supported_host_wrong_surface`
- `human_outcome_not_recorded`
- `login_wall_detected`

### Redirect rules

The plan must define behavior for:

- multi-hop tracking redirects
- unknown intermediate hosts
- redirect loops
- login-before-redirect states
- supported host reached but wrong surface detected

These should produce structured errors or structured handoffs, not generic
failures.

### Checkpoint monotonicity

Today `record_attempt()` validates checkpoint membership but not legal
progression ordering. The deepened plan should require monotonic checkpoint
progression for new manual-assist and redirect-heavy flows.

## Data Model and Artifact Changes

### Plan/status fields

Add optional fields first:

- `board`
- `origin_board`
- `surface_policy`
- `apply_host`
- `redirect_chain`
- `handoff_kind`
- `executor_backend`

Schema files to extend tolerantly:

- [schemas/application-plan.schema.json](/Users/simons/job-hunt/schemas/application-plan.schema.json)
- [schemas/application-status.schema.json](/Users/simons/job-hunt/schemas/application-status.schema.json)
- [schemas/application-attempt.schema.json](/Users/simons/job-hunt/schemas/application-attempt.schema.json)
- lead schema, if new origin metadata is stored there

### Enum/state rollout contract

The LinkedIn path does not only add optional fields. It also adds new enum
values and state transitions that existing consumers do not currently accept.

These include:

- `surface = linkedin_easy_apply_assisted`
- attempt status `paused_manual_assist`
- lifecycle state `awaiting_human_action`
- any new event types such as `manual_assist_deferred`

Those cannot be rolled out with a generic "optional fields first" strategy.
They require coordinated consumer-first sequencing:

1. Extend schemas, `_PRIORITY_LADDER`, `lead_state_from_attempt()`, event
   handling, and tests so the repo can read/write the new enum members.
2. Keep LinkedIn producers disabled until those consumers land.
3. Only then allow `prepare-application` / `apply-posting` to emit LinkedIn
   drafts containing the new states.

Concrete implication:

- no phase may write `linkedin_easy_apply_assisted`, `paused_manual_assist`, or
  `awaiting_human_action` artifacts until the runtime and schema consumers are
  already updated to accept them.

### Correlation keys

LinkedIn-origin redirects need both the origin URL and the final execution URL
to support later confirmation matching.

Recommended correlation shape:

```json
{
  "origin_board": "linkedin",
  "origin_posting_url": "https://www.linkedin.com/jobs/view/...",
  "posting_url": "https://boards.greenhouse.io/example/jobs/123",
  "redirect_chain": ["https://...", "https://..."],
  "company": "...",
  "title": "...",
  "submitted_at": null
}
```

### Redaction/canonicalization

Persist only canonicalized, sanitized redirect URLs.

Do not persist:

- raw LinkedIn DOM dumps
- cookies
- auth headers
- localStorage/sessionStorage contents
- verification payloads
- OTPs or credential material

LinkedIn manual-assist screenshots and bundles should be treated as PII-bearing
artifacts and remain gitignored alongside the existing checkpoints/attempts.

## CLI Contract Changes

### Existing commands to extend

Do not create a parallel CLI surface if existing commands can handle this.

Extend:

- `extract-lead`
- `prepare-application`
- `apply-posting`
- `record-attempt`
- `apply-batch`

### `apply-posting` output

Today `apply-posting()` always emits an automation-style handoff bundle. The
deepened plan requires two output shapes under one command:

1. `handoff_kind=automation_playbook`
2. `handoff_kind=manual_assist`

Manual-assist bundle fields:

- `surface_policy`
- `operator_checklist`
- `field_summary`
- `review_items`
- `resume_path`
- `cover_letter_path`
- `outcome_recording_instructions`

## Batch Behavior

### LinkedIn batch eligibility

`apply-batch --source linkedin` should only auto-run drafts whose execution
surface is a supported ATS host.

LinkedIn-hosted manual-assist drafts should be:

- prepared if explicitly requested, or
- deferred into a manual queue, or
- excluded from batch auto-run

They should **not** be counted as failures.

### Re-selection rules

Drafts already in `awaiting_human_action` must be excluded from later batch
selection unless the user explicitly refreshes or reopens them.

### Daily cap semantics

Daily caps should count actual submissions, not manual-assist deferrals.

## File-Level Implementation Plan

### Phase 1: Extract the board seam without changing behavior

Goal: isolate board-specific logic while keeping Indeed behavior identical.

Files:

- add `src/job_hunt/boards/base.py`
- add `src/job_hunt/boards/registry.py`
- add `src/job_hunt/boards/indeed.py`
- modify `src/job_hunt/application.py`
- modify `src/job_hunt/playbooks.py`
- modify `playbooks/application/generic-application.md`
- update routing tests in `tests/test_phase4_application.py`

Deliverables:

- `detect_surface()` delegates to board registry
- `playbook_for_surface()` delegates to board registry
- existing tests still pass with unchanged Indeed semantics

### Phase 2: Add LinkedIn origin support via manual/local intake

Goal: support LinkedIn as an origin board without fetch-based LinkedIn access.

Files:

- add `src/job_hunt/boards/linkedin.py`
- modify lead schema
- modify `src/job_hunt/core.py` only if new lead metadata needs CLI exposure
- add tests for LinkedIn-origin lead metadata through `extract-lead`

Deliverables:

- LinkedIn-origin leads can be created from local/manual input files
- local/manual intake contract explicitly supports:
  - direct ATS execution URLs, or
  - sanitized redirect chains ending at ATS hosts
- `ingest-url` and `discover-jobs` still hard-fail LinkedIn

### Phase 3: Extend consumers for new enums/states before any LinkedIn producer writes them

Goal: make schemas and runtime state handling safe for LinkedIn-specific
surfaces and manual-assist states.

Files:

- modify `src/job_hunt/application.py`
- modify `schemas/application-plan.schema.json`
- modify `schemas/application-status.schema.json`
- modify `schemas/application-attempt.schema.json`
- update tests that assert surface/status/event enums

Deliverables:

- optional `origin_board`, `surface_policy`, `handoff_kind`,
  `redirect_chain`, and `executor_backend` fields
- runtime accepts new enum members such as `linkedin_easy_apply_assisted`,
  `paused_manual_assist`, and `awaiting_human_action`
- tolerant reads for old artifacts
- LinkedIn producers remain disabled until this phase is complete

### Phase 4: Ship LinkedIn manual-assist flow

Goal: make LinkedIn-hosted Easy Apply usable without any automation on
`linkedin.com`.

Files:

- add `playbooks/application/linkedin-easy-apply-assisted.md`
- modify `src/job_hunt/application.py`
- modify `playbooks/application/generic-application.md`
- modify `src/job_hunt/playbooks.py`
- add LinkedIn manual-assist pipeline tests

Deliverables:

- `apply-posting()` emits `handoff_kind=manual_assist`
- new `paused_manual_assist` / `awaiting_human_action` behavior
- operator-assist bundle with field summary and outcome recording path

### Phase 5: Reuse ATS playbooks for LinkedIn-origin redirects

Goal: automate only the external-host portion of LinkedIn-origin jobs.

Files:

- modify `src/job_hunt/application.py`
- modify relevant batch-selection tests
- add LinkedIn-origin redirect pipeline tests

Deliverables:

- LinkedIn-origin redirect jobs resolve directly to existing ATS surfaces
- `apply-batch --source linkedin` processes only ATS-eligible drafts

### Phase 6: Add executor backend seam

Goal: create a shared handoff vocabulary for Codex browser and Claude Chrome.

Files:

- add `src/job_hunt/executors/base.py`
- add `src/job_hunt/executors/codex_browser.py`
- add `src/job_hunt/executors/claude_chrome.py`
- modify `src/job_hunt/application.py`

Deliverables:

- additive metadata only in v1
- no requirement to rewrite current playbooks into backend-neutral specs yet

### Phase 7: Optional confirmation extensions

Goal: improve LinkedIn-origin and external-ATS confirmation matching.

Files:

- modify `src/job_hunt/confirmation.py`
- add confirmation tests if LinkedIn-origin metadata is used in matching

Deliverables:

- origin URL plus final execution URL can both participate in correlation

## Acceptance Criteria

### Positive criteria

- A LinkedIn-origin lead can be created from a local/manual input file.
- A LinkedIn-origin draft only becomes ATS-automatable when the local/manual
  intake artifact includes a deterministic final execution URL or sanitized
  redirect chain ending at a supported ATS host.
- `prepare-application` can produce a schema-valid plan for LinkedIn-origin
  leads.
- LinkedIn-origin jobs that resolve to Greenhouse/Lever/Workday/Ashby reuse the
  existing ATS playbooks without losing `origin_board=linkedin`.
- A LinkedIn-hosted Easy Apply draft can enter `awaiting_human_action` without
  being treated as failed or untouched.
- A human can record one of three LinkedIn manual-assist outcomes:
  submitted, aborted, or unknown.
- Re-running batch does not reselect drafts already in
  `awaiting_human_action`.
- Redirect chains are preserved in sanitized form and external ATS
  confirmations can still correlate back to the LinkedIn-origin draft.

### Negative criteria

- `is_hard_fail_url("https://www.linkedin.com/jobs/...")` remains `true`.
- `ingest-url` on a LinkedIn URL continues to return a structured hard-fail.
- `discover-jobs` does not poll LinkedIn in v1.
- A LinkedIn-origin job without a deterministic external apply target is never
  auto-routed into an ATS playbook.
- No executor call against `linkedin.com` application surfaces is allowed.
- `linkedin_easy_apply_assisted` never emits an automation handoff bundle.
- `apply-batch --source linkedin` never auto-drives LinkedIn-hosted Easy Apply.
- No runtime config can enable automation on `linkedin.com`.
- No live automated browser test clicks, types, uploads, or reads DOM on
  `linkedin.com`.

## Risks

- **Policy risk**: if the plan accidentally reopens fetch/discovery or browser
  automation on `linkedin.com`, it will violate the repo's tightened policy
  posture.
- **State-model risk**: without `awaiting_human_action`, manual-assist drafts
  will look untouched or aborted.
- **Schema drift risk**: new fields must remain optional until all producers
  and consumers are upgraded.
- **Scope risk**: backend-neutral execution should remain an additive seam, not
  a prerequisite for landing LinkedIn support.

## Recommendation

Approve this feature as:

**multi-board architecture + LinkedIn-origin support + manual-assist on
LinkedIn-hosted flows + automated reuse only after redirect to supported ATS
hosts**

Do **not** approve it as "port the Indeed automation to LinkedIn."

That gives the repo a safe and useful outcome:

- LinkedIn becomes a first-class **origin board**
- external redirects get immediate leverage from existing playbooks
- LinkedIn-hosted flows become repeatable and auditable without automation
- the repo gets the right architecture for future boards

## Research Notes

- Repo files reviewed:
  - [src/job_hunt/application.py](/Users/simons/job-hunt/src/job_hunt/application.py)
  - [src/job_hunt/playbooks.py](/Users/simons/job-hunt/src/job_hunt/playbooks.py)
  - [src/job_hunt/core.py](/Users/simons/job-hunt/src/job_hunt/core.py)
  - [schemas/application-plan.schema.json](/Users/simons/job-hunt/schemas/application-plan.schema.json)
  - [schemas/application-attempt.schema.json](/Users/simons/job-hunt/schemas/application-attempt.schema.json)
  - [schemas/application-status.schema.json](/Users/simons/job-hunt/schemas/application-status.schema.json)
  - [playbooks/application/generic-application.md](/Users/simons/job-hunt/playbooks/application/generic-application.md)
  - [playbooks/application/indeed-easy-apply.md](/Users/simons/job-hunt/playbooks/application/indeed-easy-apply.md)
- Internal learnings applied:
  - [human-in-the-loop on submit](../../docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md)
  - [Indeed directApply surface detection](../../docs/solutions/integration-issues/indeed-surface-detection-via-directapply.md)
  - [extend CLI without breaking compatibility](../../docs/solutions/workflow-issues/extend-cli-with-new-modules-without-breaking-backward-compat.md)
  - [ship tolerant consumers before strict producers](../../docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md)
