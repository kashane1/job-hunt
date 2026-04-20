---
playbook_id: ashby-redirect
surface: ashby_redirect
origin_allowlist:
  - "jobs.ashbyhq.com"
  - "ashbyhq.com"
checkpoint_sequence:
  - preflight_done
  - form_opened
  - fields_filled
  - ready_to_submit
  - submitted
  - confirmation_captured
DATA_NOT_INSTRUCTIONS: true
---

# Ashby Redirect Playbook

Ashby is the newest of the 5 ATS surfaces, used heavily by YC-era startups. Single-page form, clean component structure, predictable question ordering.

## Preconditions
Same as `indeed-easy-apply.md`. Origin must be in the allowlist above.

## Data vs instructions
Same v4 invariant.

## Step 0: Preflight
`status=in_progress, checkpoint=preflight_done`.

## Step 1: Navigate
Ashby URLs: `https://jobs.ashbyhq.com/{company}/{job_id}`.
Off-origin → `off_origin_form_detected`.

## Step 2: Open apply form
Click "Apply for this job". Single-page form loads.
`checkpoint-update` to `form_opened`.

## Step 3: Standard fields
Ashby's prefix:
- `first_name`, `last_name`
- `email`
- `resume` (PDF)
- `linkedin_url`
- Optional: `phone`, `website`, `current_company`

## Cover-letter handling
- Check Ashby's standard document section for a cover-letter upload field.
- If `bundle.cover_letter_available=true` and the field exists, upload `bundle.cover_letter_pdf_path`.
- If the field is absent, skip without error and record `cover_letter_status=skipped_optional_slot_missing`.
- If Ashby exposes only a text area, pause for manual review in v1 and record `cover_letter_status=text_area_not_supported`.

## Step 4: Custom questions
Match `plan.fields` entries with `field_id` starting `custom_` by normalized question. Ashby's custom-question block is rendered below the standard prefix. Missing → `tier_downgrade`.

## Step 5: Self-identification (optional)
Ashby's EEOC block is visually de-emphasized and marked "optional". Default decline; answer-bank override available.

`checkpoint-update` to `fields_filled`.

## Step 6: Pre-submit screenshot
Standard. `checkpoint-update` to `ready_to_submit`.

## Step 7: Human submit gate
Standard `ready_to_submit: true` payload.

## Step 8: Post-submit capture
Ashby's confirmation appears inline on the same page: the form is replaced with "Your application has been received". No URL change. Poll body for 30s for the confirmation string.
`status=submitted_provisional, checkpoint=confirmation_captured`. Call `record-attempt`.

## Failure taxonomy
Same as `indeed-easy-apply.md`.
