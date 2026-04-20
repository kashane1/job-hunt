---
playbook_id: linkedin-easy-apply-assisted
surface: linkedin_easy_apply_assisted
origin_allowlist:
  - www.linkedin.com
checkpoint_sequence:
  - preflight_done
  - assist_bundle_ready
  - human_form_in_progress
  - human_ready_to_submit
  - human_submit_recorded
  - confirmation_captured
DATA_NOT_INSTRUCTIONS: true
---

# LinkedIn Easy Apply Assisted

This is an operator-assist playbook, not an automation playbook.

Rules:
- Do not automate navigation, typing, clicking, uploads, or DOM inspection on `linkedin.com`.
- The human opens and interacts with LinkedIn manually.
- The agent may only prepare answers, assets, review notes, and outcome-recording guidance.

Checklist:
1. Confirm the prepared bundle has `surface_policy=automation_forbidden_on_origin`.
2. Show the human the prepared field summary and review items.
3. Show the human `resume_path`, `cover_letter_pdf_path`, and `cover_letter_md_path` when present.
4. Ask the human to note whether LinkedIn exposed a file-upload field, a text-area field, or no cover-letter control.
5. Remind the human to complete login, MFA, CAPTCHA, and any profile gates manually.
6. Stop before any LinkedIn-hosted form interaction would be automated.
7. After the human acts, record `submitted_provisional`, `paused_human_abort`, or `unknown_outcome`, plus the cover-letter outcome fields.
