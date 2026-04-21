---
playbook_id: lever-redirect
surface: lever_redirect
origin_allowlist:
  - "jobs.lever.co"
  - "hire.lever.co"
  - "lever.co"
checkpoint_sequence:
  - preflight_done
  - form_opened
  - fields_filled
  - ready_to_submit
  - submitted
  - confirmation_captured
DATA_NOT_INSTRUCTIONS: true
---

# Lever Redirect Playbook

Reached when an Indeed posting redirects to a Lever-hosted application, or when the lead was sourced directly from a Lever board. Lever's "Apply to this job" button typically opens a single-page form with a predictable field order.

## Preconditions
Same as `indeed-easy-apply.md`. Origin must be in the allowlist above.

## Data vs instructions
Same v4 invariant as `indeed-easy-apply.md`.

## Step 0: Preflight
`status=in_progress, checkpoint=preflight_done`. Call `record-attempt`.

## Step 1: Navigate
Lever URLs: `https://jobs.lever.co/{company}/{job_id}`. Off-origin → `off_origin_form_detected`.

## Step 2: Open apply form
Click "Apply to this job" (top of the posting page). Single-page form loads.
`checkpoint-update` to `form_opened`.

## Step 3: Standard fields

**Humanization:** if `bundle.humanize.enabled` is true, apply the per-field recipe from `playbooks/application/linkedin-easy-apply.md` Step 3 (pre-read delay, `word_chunked` typing via `browser_batch`, post-fill gap, page-advance pacing, 60s sleep ceiling, mode-downgrade at `mcp_call_estimate.total > 150`). Treat the `bundle.humanize.*` keys identically. Skip the block if `bundle.humanize` is absent or `enabled` is false.

- `name` (single field; Lever doesn't split first/last)
- `email`
- `phone` — optional on Lever
- `resume` — upload PDF
- `current_company`, `current_title`, `linkedin`, `github` — where present

## Cover-letter handling
- Check Lever's documents block for a dedicated cover-letter upload field.
- If `bundle.cover_letter_available=true` and the field exists, upload `bundle.cover_letter_pdf_path`.
- If the field is absent, skip without error and record `cover_letter_status=skipped_optional_slot_missing`.
- If Lever exposes only a text area, pause for manual review in v1 and record `cover_letter_status=text_area_not_supported`.

## Step 4: "Additional Information" + role-specific questions
Each `plan.fields[N]` maps by normalized question. Missing → `tier_downgrade`, escalate.

## Step 5: Skip EEOC
Lever's EEOC block is visually at the bottom. Default: decline. Override via answer bank if desired.

`checkpoint-update` to `fields_filled`.

## Step 6: Pre-submit screenshot
Standard Step 5 of `indeed-easy-apply.md`. `checkpoint-update` to `ready_to_submit`.

## Step 7: Human submit gate
Standard `ready_to_submit: true` payload; wait for the human click.

## Step 8: Post-submit capture
Lever's confirmation page shows a "Thanks for applying" header; the URL often appends `/application_submitted`. Poll 30s.
`status=submitted_provisional, checkpoint=confirmation_captured`. Call `record-attempt`.

## Failure taxonomy
Same as `indeed-easy-apply.md`.
