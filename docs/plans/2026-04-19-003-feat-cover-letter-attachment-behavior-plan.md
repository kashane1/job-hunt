---
title: "feat: Add explicit cover-letter attachment behavior to application preparation and playbooks"
type: feat
status: active
date: 2026-04-19
origin: docs/brainstorms/2026-04-16-indeed-auto-apply-brainstorm.md
notes: "Higher-priority follow-on to docs/plans/2026-04-19-002-cover-letter-phase-5-gap-list.md. This plan addresses attachment behavior and asset plumbing before the later normalized-fragment layer."
deepened_on: 2026-04-19
---

# feat: Add Explicit Cover-Letter Attachment Behavior to Application Preparation and Playbooks

## Enhancement Summary

**Deepened on:** 2026-04-19
**Research passes used:** architecture-strategist, spec-flow-analyzer,
data-integrity-guardian, learnings-researcher

### Key Improvements

1. Make generated-content records the canonical asset source of truth, with
   `plan.json` storing references and policy rather than a second path-heavy
   manifest.
2. Define explicit asset-resolution and PDF-fallback behavior so
   `prepare-application` can degrade gracefully when cover-letter generation or
   PDF export fails.
3. Add a per-surface attachment matrix and a shared cover-letter decision
   contract so playbooks describe timing, but `application.py` remains the
   canonical home for attachment policy.
4. Tighten the attempt/report contract so cover-letter outcomes are written at
   a defined point in the flow, remain compatible with byte-immutable attempt
   files, and avoid leaking long-lived path metadata into durable reports.

### New Considerations Discovered

- `prepare_application()` and `apply_posting()` are not the only consumers:
  existing generated-content records, draft asset selection, playbooks, and
  report writers all need the same rollout sequence.
- The repo should distinguish:
  - asset unavailable
  - optional slot absent
  - required slot but no usable asset
  - text area present but unsupported in v1
- LinkedIn manual-assist needs both real file paths at handoff time and
  durable outcome/status fields after the human finishes.
- Raw file paths are useful in transient execution bundles, but durable
  reports should prefer content ids and status fields over persistent path
  copies.

## Review Findings Addressed

### P1: Avoid a second asset source of truth

The plan originally risked duplicating asset paths across generated-content
records, draft selection, and `plan.json`. The fix is to keep generated-content
canonical for concrete paths and keep `plan.json` reference-oriented.

### P1: Specify real manual-assist asset resolution

The current `apply_posting()` bundle still hardcodes empty manual-assist asset
paths. The plan now requires `apply-posting` to resolve real values from
content ids and to use explicit nullable/status fields instead of ambiguous
empty strings.

### P1: Update attempt/report contracts in lockstep

Attachment outcome persistence must land together across attempt schema, report
schema, and report rendering. The plan now calls that out explicitly so the
machine-readable and human-readable artifacts do not drift.

### P2: Preserve byte-immutable attempt history

Attachment outcomes discovered later must be recorded via a new attempt record,
not by mutating an existing attempt file. The persistence contract now makes
that sequencing explicit.

## Overview

The repo already has cover-letter generation, but the current application
pipeline does not reliably carry a generated cover letter into the prepared
draft, handoff bundle, or per-surface playbooks.

That makes the current behavior inconsistent:

- cover letters can be generated
- some playbooks mention uploading them
- the current `prepare-application` / `apply-posting` path does not actually
  provide a durable cover-letter asset path to the executor or manual-assist
  flow

This plan adds a first-class cover-letter attachment policy and asset-plumbing
layer to the application workflow.

It is intentionally higher priority than the later normalized-fragment work in
`docs/plans/2026-04-19-002-cover-letter-phase-5-gap-list.md`.

## Why This Comes First

The Phase 5 fragment-layer plan is still valid, but it is primarily a
generation-quality and auditability improvement.

This plan fixes a more immediate workflow gap:

- whether a generated cover letter is available during application prep
- whether the agent or human knows when to attach it
- whether the system records why it was attached, skipped, or unavailable

If the repo cannot consistently answer those three questions, better fragment
selection alone does not improve real application execution.

## Relevant Prior Context

The Indeed auto-apply brainstorm already assumed draft artifacts would carry
resume and cover-letter assets as first-class preparation outputs
(`docs/brainstorms/2026-04-16-indeed-auto-apply-brainstorm.md`).

The current repo also carries two useful implementation learnings:

- `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md`
  - add tolerant schema/consumer support before making producers depend on new
    fields
- `docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md`
  - policy-sensitive surfaces need explicit manual-assist artifacts instead of
    leaving humans to infer what to do
- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
  - preserve the human-submit boundary even when document handling becomes more
    explicit
- `docs/solutions/workflow-issues/extend-cli-with-new-modules-without-breaking-backward-compat.md`
  - new schema members and bundle fields should ship optional-first and be read
    via tolerant `.get()` consumers

This plan follows both.

## Problem Statement

Today the application pipeline has four concrete gaps:

1. `prepare_application()` writes `plan.json`, but it does not generate or
   persist a cover-letter asset block for downstream execution.
2. `application-plan.schema.json` has no explicit place for cover-letter asset
   paths, selected content ids, or attachment decisions.
3. `apply_posting()` does not currently provide actual cover-letter asset
   fields to either the automated-playbook bundle or the LinkedIn
   manual-assist bundle.
4. Playbooks are inconsistent about cover letters:
   - Greenhouse explicitly mentions uploading one when present.
   - Indeed does not describe when to look for an optional cover-letter slot.
   - Lever, Ashby, and Workday do not define a clear rule.
   - LinkedIn manual-assist prepares no actual `cover_letter_path` for the
     human.

The result is ambiguity in exactly the place where the repo should be most
reliable: application execution and audit trails.

## Goals

- Generate or resolve a durable cover-letter asset during
  `prepare-application`.
- Surface that asset in `plan.json` and the `apply-posting` handoff bundle.
- Define one repo-wide attachment decision rule that each surface follows.
- Make Indeed behavior explicit for optional cover-letter slots.
- Make LinkedIn manual-assist provide the human with the actual asset path.
- Record attachment outcome and reason in attempt/report artifacts.

## Non-Goals

- This plan does not redesign cover-letter writing quality.
- This plan does not replace the Phase 5 fragment-layer plan.
- This plan does not loosen the human-submit invariant.
- This plan does not add automation on `linkedin.com`.
- This plan does not require support for every possible text-area-only cover
  letter widget in v1.

## Hard Invariants

1. The agent still never clicks the final Submit button.
2. Cover letters remain optional unless the target form makes them required.
3. Lack of a cover-letter slot must not be treated as an error when the slot is
   genuinely absent or optional.
4. The repo must record whether a cover letter was:
   - attached
   - intentionally skipped
   - unavailable
   - deferred to the human
5. LinkedIn-hosted flows remain manual-assist only.

## Decision

### Repo-wide cover-letter attachment rule

The workflow should use this order on every supported surface:

1. Prepare a generated cover-letter asset if generation succeeds.
2. Carry asset references into `plan.json` and resolve concrete paths in the
   `apply-posting` handoff bundle.
3. During form execution, check for a cover-letter control at the appropriate
   surface-specific point.
4. If a file-upload control exists and the cover-letter asset is available,
   upload it.
5. If no file-upload control exists, skip without error and record the reason.
6. If the only available input is a freeform text area, do not silently invent
   a new behavior. Either:
   - defer to a clearly documented surface-specific implementation, or
   - mark the field for manual review / manual assist in v1.

### “Near the end” rule

The default preference is to attach cover letters near the end of the
application flow, but some surfaces collect documents earlier.

The operational rule should be:

- prefer the latest safe document-upload point on the surface
- if the surface only offers the attachment slot early, upload there and verify
  before the human submit gate that the intended asset is still attached

This preserves the user preference without fighting actual ATS layouts.

### Default upload format

Use a rendered PDF cover letter as the default upload artifact in v1.

Store at least:

- generated-content id
- PDF export status
- preferred upload kind

`plan.json` should not become a second full asset manifest. The generated
content record remains the canonical source of file paths; the application plan
stores the reference and execution policy needed to resolve the right path at
handoff time.

Future DOCX support can be added later if a specific ATS proves it is needed.

### Canonical asset source of truth

The repo already has three related layers:

1. generated-content records under `data/generated/...`
2. draft-level asset selection metadata
3. `plan.json` for browser execution and handoff

This plan should keep those responsibilities separate:

- generated-content records stay canonical for concrete artifact paths and
  export outputs
- draft/profile selection stays canonical for which asset/content ids were
  chosen for this application
- `plan.json` stores reference fields and policy needed by execution

That avoids three drifting copies of the same `md_path` / `pdf_path` data.

### Asset resolution and fallback contract

The execution contract should be explicit:

1. `prepare_application()` resolves or generates a cover-letter content record.
2. It attempts PDF export for that content.
3. `plan.json` records reference-level facts, not duplicate path copies.
4. `apply_posting()` resolves actual file paths from those references when it
   builds the transient handoff bundle.

Failure semantics should also be explicit:

- markdown generation fails:
  `cover_letter_available = false`; do not abort the whole draft unless the
  surface later proves the field is required
- markdown succeeds but PDF export fails:
  preserve the content id, mark `pdf_export_status = failed`, and route to
  either optional skip or manual review depending on the surface
- old `plan.json` with no cover-letter block:
  treat as backward-compatible `asset_unavailable`, not as a schema error
- manual-assist bundle generation:
  empty strings should be replaced by explicit nullable fields and/or explicit
  availability/status flags

## Proposed Data Model

Extend `schemas/application-plan.schema.json` with an optional
reference-oriented asset block plus cover-letter policy.

Suggested shape:

```json
{
  "generated_asset_refs": {
    "resume": {
      "content_id": "resume-content-id",
      "preferred_upload_kind": "pdf"
    },
    "cover_letter": {
      "content_id": "cover-letter-content-id",
      "available": true,
      "generation_status": "generated",
      "pdf_export_status": "ready",
      "preferred_upload_kind": "pdf"
    }
  },
  "cover_letter_policy": {
    "should_attempt_attachment": true,
    "preferred_stage": "late_documents_step",
    "text_area_policy": "manual_only",
    "required_slot_without_asset_policy": "pause_for_human_review"
  }
}
```

The exact field names can be tuned during implementation, but the plan should
preserve these semantics:

- the plan knows whether a cover letter exists
- the plan knows which content id to resolve for upload
- the plan knows whether text-area entry is allowed or manual-only
- the plan does not become the canonical long-lived copy of every asset path

The handoff bundle, not `plan.json`, is where resolved execution paths should
appear. That bundle can safely include concrete values such as:

- `resume_path`
- `resume_upload_kind`
- `cover_letter_pdf_path`
- `cover_letter_md_path`
- `cover_letter_available`
- `cover_letter_policy`
- `cover_letter_review_note`

## Handoff Bundle Changes

`apply-posting` should include explicit asset fields for both automated and
manual-assist flows.

Minimum additions:

- `resume_path`
- `resume_upload_kind`
- `cover_letter_pdf_path`
- `cover_letter_md_path`
- `cover_letter_available`
- `cover_letter_policy`
- `cover_letter_review_note`

For LinkedIn `manual_assist`, these fields must contain real values when a
cover letter exists. Prefer `null` plus an explicit status/reason over empty
strings, so the bundle can distinguish:

- not wired yet
- unavailable
- optional skip
- manual review required

## Per-Surface Behavior

### Attachment matrix

| Surface | When to check | Preferred artifact | If only text area exists | If no slot exists | If slot is required but asset unavailable |
|---|---|---|---|---|---|
| Indeed Easy Apply | after the main form opens, before final review | cover-letter PDF | pause for manual review in v1 | record `slot_not_present_optional` | pause tier_2 / manual review |
| Greenhouse | at the document-upload portion of the form | cover-letter PDF | pause for manual review in v1 | record `slot_not_present_optional` | pause tier_2 / manual review |
| Lever | at the standard documents block | cover-letter PDF | pause for manual review in v1 | record `slot_not_present_optional` | pause tier_2 / manual review |
| Ashby | at the standard documents block | cover-letter PDF | pause for manual review in v1 | record `slot_not_present_optional` | pause tier_2 / manual review |
| Workday | only at a clearly separate optional attachment step | cover-letter PDF when supported; otherwise manual review | manual review required | record `slot_not_present_optional` when genuinely absent | manual review required |
| LinkedIn manual assist | human inspects the available document/text controls | show both PDF and markdown paths | human decides and reports which control existed | human records none-present outcome | human decides how to proceed and records the outcome |

The matrix should live in the plan so each playbook does not have to reinvent
the shared policy.

### Indeed Easy Apply

Indeed should explicitly check for an optional cover-letter upload or document
slot after the main form is opened and before the final human submit gate.

Rule:

- if Indeed exposes an optional cover-letter upload field, upload the prepared
  PDF
- if Indeed does not expose such a field, skip and record
  `slot_not_present_optional`
- if Indeed exposes only a text-area cover-letter prompt, escalate to manual
  review in v1 unless a later implementation explicitly supports pasting the
  generated content

This is the main user-facing gap the repo should close first.

### Greenhouse

Greenhouse already conceptually supports a `cover_letter` upload field.

Rule:

- keep cover-letter upload optional
- upload when the field is present and the asset is available
- skip without error when the field is absent on that specific posting

### Lever

Lever does not guarantee a cover-letter field on every form.

Rule:

- check for a dedicated cover-letter upload field
- upload when present
- otherwise skip and record `slot_not_present_optional`

### Ashby

Ashby should follow the same pattern as Lever:

- attach when the posting exposes a cover-letter field
- otherwise skip and record the reason

### Workday

Workday remains tier_2 and more constrained.

Rule:

- prefer document upload only when Workday exposes a clearly separate optional
  cover-letter attachment control
- do not force new text-area automation in v1
- if Workday only offers a manual text box or an ambiguous multi-step document
  flow, surface the prepared cover-letter asset to the human and record
  `manual_review_required`

### LinkedIn Easy Apply Assisted

LinkedIn remains manual-assist only.

Rule:

- provide the human with the actual cover-letter PDF path and markdown path
- explicitly instruct the human to check whether LinkedIn offers:
  - a file attachment slot
  - a text-area cover-letter box
  - no cover-letter control at all
- record which of those cases occurred after the human completes the flow

## Audit And Reporting

Every attempt should gain explicit cover-letter outcome fields.

Suggested attempt/report metadata:

- `cover_letter_status`
  - `attached`
  - `skipped_optional_slot_missing`
  - `skipped_asset_unavailable`
  - `manual_review_required`
  - `text_area_not_supported`
- `cover_letter_surface_field_type`
  - `file_upload`
  - `text_area`
  - `none`
  - `unknown`
- `cover_letter_path`
- `cover_letter_content_id`
- `cover_letter_notes`

This matters because “did we attach a cover letter?” is exactly the kind of
question the repo should answer from artifacts, not memory.

### Persistence contract

The persistence rule should be:

- attempt artifacts record the cover-letter decision no later than the first
  write that knows the outcome
- if the outcome changes later, write a new attempt record rather than mutating
  an existing one
- durable reports prefer `cover_letter_content_id` and status/reason fields
  over raw file paths
- transient handoff bundles may include concrete file paths because they are
  execution-only artifacts

Suggested durable fields:

| Field | Attempts | Reports | Notes |
|---|---|---|---|
| `cover_letter_status` | yes | yes | `attached`, `skipped_optional_slot_missing`, `skipped_asset_unavailable`, `manual_review_required`, `text_area_not_supported` |
| `cover_letter_surface_field_type` | yes | yes | `file_upload`, `text_area`, `none`, `unknown` |
| `cover_letter_content_id` | yes | yes | durable reference |
| `cover_letter_reason_code` | yes | yes | machine-readable skip/escalation reason |
| `cover_letter_path` | optional | no | transient or attempt-only if needed for local debugging |
| `cover_letter_notes` | optional | optional | human-readable context |

`application-attempt.schema.json`, `application-report.schema.json`, and
human-readable report rendering should be updated together so the machine and
operator views do not diverge.

## Implementation Plan

### Phase 1: Add tolerant readers and shared asset-resolution support

- extend `schemas/application-plan.schema.json` with optional reference fields
- add/extend helpers that resolve generated-content ids into execution-time
  paths
- keep all new members optional for backward compatibility
- update `apply_posting()` and any plan readers to treat absent fields as
  `asset_unavailable`, not as hard errors

Acceptance:

- old plan artifacts continue validating
- new plans can carry cover-letter reference data without breaking old
  consumers
- the repo has one canonical place to resolve concrete asset paths

### Phase 2: Generate and persist cover-letter references during prepare-application

- update `src/job_hunt/application.py:prepare_application()` to:
  - resolve or generate a cover letter for the lead
  - attempt PDF export via the existing export path
  - persist content ids and export status into `plan.json`
  - record a non-fatal unavailable state if generation or export fails
- keep resume generation behavior intact
- keep `application.py` as a thin coordinator over generation/pdf-export
  helpers rather than embedding rendering logic there

Acceptance:

- `prepare-application` writes a cover-letter reference block when generation
  succeeds
- markdown success plus PDF export failure degrades deterministically
- failure to generate a cover letter does not abort the whole draft unless the
  application surface later requires one

### Phase 3: Expose resolved asset paths in apply-posting

- update `src/job_hunt/application.py:apply_posting()` so both automated and
  manual-assist bundles resolve the actual asset paths from content ids
- stop hardcoding empty placeholder path strings in manual-assist bundles
- include both PDF and markdown cover-letter paths in the LinkedIn
  manual-assist bundle when available

Acceptance:

- LinkedIn manual-assist bundles contain real asset paths when available
- automated bundles contain enough information for playbooks to perform file
  uploads deterministically

### Phase 4: Centralize the decision rule and make playbooks explicit about timing

- add a shared helper/policy seam for cover-letter decisioning in the
  execution layer
- update the per-surface playbooks to describe:
  - when to check for the cover-letter control
  - which artifact kind is preferred
  - whether file upload is supported
  - what to do if only a text area exists
  - what reason code to record when skipped

Acceptance:

- every supported surface has a documented cover-letter decision path
- Indeed explicitly checks for optional cover-letter upload behavior
- LinkedIn manual-assist explicitly tells the human what to inspect
- shared decision logic does not drift across six playbooks

### Phase 5: Record attachment outcomes in attempts and reports

- extend attempt/report writing so cover-letter outcome is persisted
- update attempt/report schemas and report markdown together
- ensure manual-assist and automated flows both write the same shape
- keep attempt files byte-immutable; later-discovered outcomes write a new
  attempt instead of mutating history

Acceptance:

- application artifacts can answer whether a cover letter was attached
- skip reasons are visible without reading freeform logs
- durable reports do not rely on raw path copies to explain the outcome

### Phase 6: Update docs and regression tests

- update `docs/guides/indeed-auto-apply.md`
- add tests covering:
  - plan generation with cover-letter reference data
  - manual-assist bundle includes actual paths
  - optional-slot surfaces skip cleanly
  - cover-letter generation failure degrades gracefully
  - PDF export failure degrades gracefully
  - required slot without usable asset escalates predictably
  - per-surface reason codes

Acceptance:

- user-facing docs describe where cover letters are checked and when they are
  skipped
- automated tests lock in the intended behavior

## Likely Files To Change

### Primary code

- `src/job_hunt/application.py`
- `src/job_hunt/generation.py`
- `src/job_hunt/core.py`
- `src/job_hunt/pdf_export.py`
- possibly `src/job_hunt/profile.py`

### Schemas

- `schemas/application-plan.schema.json`
- `schemas/generated-content.schema.json`
- possibly:
  - `schemas/application-attempt.schema.json`
  - `schemas/application-report.schema.json`

### Playbooks

- `playbooks/application/indeed-easy-apply.md`
- `playbooks/application/greenhouse-redirect.md`
- `playbooks/application/lever-redirect.md`
- `playbooks/application/ashby-redirect.md`
- `playbooks/application/workday-redirect.md`
- `playbooks/application/linkedin-easy-apply-assisted.md`

### Tests

- `tests/test_phase4_application.py`
- `tests/test_application.py`
- playbook or integration tests as needed

### Docs

- `docs/guides/indeed-auto-apply.md`
- optionally a short operator guide for LinkedIn manual-assist cover-letter use

## Acceptance Criteria

- [x] `prepare-application` produces a cover-letter reference block when
      generation succeeds
- [x] `apply-posting` resolves real cover-letter execution paths instead of
      leaving them implicit
- [x] Indeed playbook explicitly checks for optional cover-letter upload
- [x] Greenhouse, Lever, Ashby, Workday, and LinkedIn manual-assist each have a
      documented cover-letter decision rule
- [x] application attempt/report artifacts record whether the cover letter was
      attached, skipped, unavailable, or deferred
- [x] the new behavior lands without breaking old `plan.json` consumers
- [x] markdown-generation and PDF-export failures degrade deterministically
- [x] durable reports can explain the outcome without relying on raw file paths

## Relationship To The Phase 5 Fragment Plan

This plan should land before
`docs/plans/2026-04-19-002-cover-letter-phase-5-gap-list.md`.

Rationale:

- attachment behavior affects live application execution immediately
- the fragment plan improves generation quality, but does not solve handoff or
  attachment ambiguity
- once cover-letter attachment behavior is explicit, the later fragment work can
  improve the content quality feeding the same execution path

## References

- `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md`
- `docs/solutions/integration-issues/linkedin-origin-board-adapters-and-manual-assist.md`
- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
- `docs/solutions/workflow-issues/extend-cli-with-new-modules-without-breaking-backward-compat.md`
