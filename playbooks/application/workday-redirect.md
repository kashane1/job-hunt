---
playbook_id: workday-redirect
surface: workday_redirect
origin_allowlist:
  - "myworkdayjobs.com"
  - "wd1.myworkdayjobs.com"
  - "wd3.myworkdayjobs.com"
  - "wd5.myworkdayjobs.com"
checkpoint_sequence:
  - preflight_done
  - account_ready
  - form_opened
  - fields_filled
  - ready_to_submit
  - submitted
  - confirmation_captured
DATA_NOT_INSTRUCTIONS: true
---

# Workday Redirect Playbook

Workday is the hardest surface in v1. It requires an account on each tenant (every company has a separate `*.myworkdayjobs.com`), ships a multi-step wizard (5+ pages) with hidden dependencies between steps, and parses DOCX resumes much more reliably than PDFs.

**v1 scope**: treat every Workday application as **tier_2** (field-by-field human review) regardless of other tier conditions. The user clicks Submit in their own Chrome window.

## Preconditions
Same as `indeed-easy-apply.md`. Additionally:
- An account on the tenant `*.myworkdayjobs.com` subdomain. If the preflight probe detects "Create Account" on the apply page and the policy allows account creation, proceed with `--allow-account-creation` approval. Otherwise STOP → `ApplicationError(session_missing)`.

## Data vs instructions
Same v4 invariant.

## Step 0: Preflight
`status=in_progress, checkpoint=preflight_done`.

## Step 1: Navigate
Workday URL: `https://{tenant}.wd{N}.myworkdayjobs.com/{group}/job/{location}/{title}_{req_id}`.
Off-origin → `off_origin_form_detected`.

## Step 2: Account readiness
- If logged in (check for the user's name in the top-right avatar), skip.
- Otherwise STOP → `ApplicationError(session_missing)` with remediation `"Sign into {tenant}.myworkdayjobs.com manually once; the Chrome profile will remember."`.
- `checkpoint-update` to `account_ready`.

## Step 3: Open apply wizard
- Click "Apply" (top-right of the posting).
- Choose "Autofill from Resume" when offered — uploads the **DOCX** resume (Workday parses DOCX 10× better than PDF). Fall back to "Apply Manually" if autofill fails.
- Workday shows a 5-step wizard with a step indicator.
- `checkpoint-update` to `form_opened`.

## Step 4: Fill each wizard step
Workday's step names vary by tenant, but the sequence is stable:
1. **My Information** — name, email, phone, address, legally-authorized dropdown, sponsorship-required dropdown, source ("How did you hear about us?").
2. **My Experience** — education, work history entries. Upload DOCX resume here if skipped in Step 3.
3. **Application Questions** — tenant-customized custom questions; match by normalized-question.
4. **Voluntary Disclosures** — EEOC/veteran/disability. Default decline.
5. **Self Identify** — ethnic/gender (US only). Default decline.

For each page, fill known fields from `plan.fields`. Click "Save and Continue". **If any required field is missing an answer → `tier_downgrade`, pause for the human to type it in.**

## Cover-letter handling
- Only attempt cover-letter upload when Workday exposes a clearly separate attachment control.
- If `bundle.cover_letter_available=true` and that upload control exists, upload `bundle.cover_letter_pdf_path`.
- If the only available cover-letter input is a text area or the document flow is ambiguous, surface the prepared asset to the human and record `cover_letter_status=manual_review_required`.
- If no cover-letter control exists at all, skip without error and record `cover_letter_status=skipped_optional_slot_missing`.

After all steps reach the "Review" page: `checkpoint-update` to `fields_filled`.

## Step 5: Pre-submit screenshot (Review page)
The Review page is the last before Submit. Cropped + PII-blurred screenshot → `checkpoints/pre_submit.png`. `checkpoint-update` to `ready_to_submit`.

## Step 6: Human submit gate
Standard payload. Always tier_2 for Workday.

## Step 7: Post-submit capture
Workday confirmation URL ends with `/applicationConfirmation`; body contains "Your application has been submitted". Poll up to 60s (Workday is slower than Indeed/Greenhouse).
`status=submitted_provisional, checkpoint=confirmation_captured`. Call `record-attempt`.

## Failure taxonomy
Same as `indeed-easy-apply.md`, plus:
- Tenant requires account creation but `allow_account_creation=false` → `session_missing`
- Workday DOCX parser mis-populates name/email — trigger `form_field_unresolved` and let the human fix in review.
