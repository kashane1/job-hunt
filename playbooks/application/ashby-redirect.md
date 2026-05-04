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

**Humanization:** if `bundle.humanize.enabled` is true, apply the per-field recipe from `playbooks/application/linkedin-easy-apply.md` Step 3 (pre-read delay, `word_chunked` typing via `browser_batch`, post-fill gap, page-advance pacing, 60s sleep ceiling, mode-downgrade at `mcp_call_estimate.total > 150`). Treat the `bundle.humanize.*` keys identically. Skip the block if `bundle.humanize` is absent or `enabled` is false.

Ashby's prefix:
- `first_name`, `last_name`
- `email`
- `resume` (PDF)
- `linkedin_url`
- Optional: `phone`, `website`, `current_company`

## Cover-letter handling
- Most Ashby forms don't expose a separate cover-letter field; the application question textareas (e.g. "Why X?", "Tell us more") serve that purpose. Fill them with tailored text per memory rules (no JD references, no defensive caveats, lead with ai-company-os).
- If a dedicated cover-letter slot exists, prefer text-area paste over file upload because `mcp__Claude_in_Chrome__file_upload` is currently blocked by the extension.
- If only a file-upload slot is available, ask Kashane to drag the PDF from `data/generated/cover-letters/`.

## Resume upload (manual)
- `mcp__Claude_in_Chrome__file_upload` returns `Not allowed` on Ashby resume inputs. **Always ask Kashane to manually upload** `data/generated/resumes/Resume-from-indeed.pdf` via the "Upload File" button or drag-and-drop on the Resume slot.
- Note: Ashby also offers an "Autofill from resume" button at the top of the form. Uploading there will prefill name/email/etc. but still requires Kashane's manual file action.

## Per-company application caps
Ashby surfaces inline "This job has application limits" notices on some boards (Vanta = 2 roles per 60 days). Read the inline notice during Step 2 and respect it. See `feedback_check_company_history.md`.

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
