# Resume Workflow Notes

Date: 2026-04-17

## Goal

Create two updated resume variants for Kashane Sakhakorn:

- one targeting mid/senior software engineering roles
- one targeting AI engineering / AI systems roles

Use the work to evaluate what `job-hunt` already supports for repeatable resume updates and what is still missing.

## What Worked Well

The repo already has several strong building blocks for repeatable resume work:

- `profile/raw/` and `normalize-profile` provide a durable place to store source materials and convert them into structured candidate data.
- The normalizer can ingest mixed source types including resumes, preferences, question banks, work notes, and `project_note` documents.
- `profile/normalized/candidate-profile.json` gives a reusable machine-readable snapshot of contact info, skills, highlights, answer-bank entries, and preferences.
- `docs/reports/profile-document-audit.md` and `docs/reports/profile-completeness.md` make it easy to see where the profile is strong or noisy.
- `src/job_hunt/ats_check.py` is already useful for validating final resume structure and length, even when no specific lead is provided.
- `data/generated/resumes/*.json` is a good artifact shape for storing final resume outputs plus provenance and ATS-check status.
- The privacy model is sound: `profile/raw/`, `profile/normalized/`, `data/generated/`, and other candidate-sensitive paths are gitignored by default.

## What We Did In This Pass

- Updated the raw identity, preferences, and master-resume source files with current BAM Global Systems context and GitHub profile data.
- Added `project_note` source docs for `ai-company-os` and `job-hunt` so AI-systems work is represented as first-class profile input, not just ad hoc prose.
- Cleaned stale company-specific and outdated timeline answers from `profile/raw/question-examples.txt`.
- Re-ran `normalize-profile`, which now produces a `100%` completeness score and a much stronger normalized candidate profile.
- Authored two final resume artifacts in `data/generated/resumes/` and verified both with the built-in ATS checker.

## Current Gaps

These are the main things still missing if we want this workflow to be truly repeatable:

### 1. No DOCX intake in the main profile pipeline

`docs/profile/README.md` explicitly says DOCX is not part of the main normalization flow yet. In practice, that means user-provided `.docx` resumes still require a manual conversion step before the repo can use them.

### 2. Resume generation is still too template-light

`src/job_hunt/generation.py` can generate resume variants, but the current renderer flattens accomplishments into one generic `Professional Experience` section. It does not model:

- role-by-role employment entries
- company names and date ranges as structured data
- selected projects as a separate first-class section
- different layout strategies for platform vs AI-targeted resumes

That is why the final resumes in this pass were authored manually, even though the repo already has a generation pipeline.

### 3. The profile model needs structured employment history

Right now, the normalized profile is strongest at:

- skills
- bullet highlights
- Q/A answers
- preferences

It does not yet have a high-trust structured object for:

- companies
- titles
- dates
- role-specific bullets

Adding a first-class `employment_history` shape would make targeted resume generation much more reliable.

### 4. The answer bank needs hygiene checks

This session exposed a real risk: old application answers in `question-examples.txt` still contained company-specific language and stale dates. The repo currently has no linter or policy check for:

- stale dates
- explicit company names inside supposedly reusable answers
- level drift such as outdated "junior" phrasing

That should become a repeatable validation step before answer-bank content is used in live applications.

### 5. Long work notes still pollute highlight extraction

The normalizer is functional, but long `work_note` files still contribute a lot of noisy bullets. Useful details are in those files, but the signal-to-noise ratio is poor compared with curated `question_bank`, `resume`, and `project_note` sources.

The repo would benefit from:

- stronger trust weighting by document type
- an ignore/deprioritize mechanism for noisy sources
- project-note specific parsing that favors curated summaries over incidental bullets

### 6. No generic "master resume target" workflow

Current generation is lead-driven. That works for job-specific tailoring, but not for maintaining a reusable set of baseline resumes such as:

- mid/senior software engineer
- AI engineer / AI systems engineer
- platform / backend engineer

The repo should support durable target profiles or reusable "resume archetypes" that can be refreshed independently of a specific job lead.

### 7. Output packaging is incomplete

The repo can export PDF through `src/job_hunt/pdf_export.py`, but that depends on optional `weasyprint` setup, which was not installed in this environment. There is also no built-in DOCX export path today.

For practical resume usage, the system should eventually support:

- markdown source of truth
- PDF export
- DOCX export

without requiring ad hoc external conversion steps.

## Recommended Next Enhancements

1. Add a structured `employment_history` source format and normalized schema.
2. Add a reusable `project_case_studies` schema for side projects and internal tools.
3. Add answer-bank linting for stale dates, company-name leakage, and outdated level language.
4. Add a lead-independent `generate-master-resume --target <archetype>` workflow.
5. Add DOCX import and DOCX export support.
6. Add trust weighting so `question_bank`, `resume`, and curated `project_note` docs outrank noisy work notes during highlight selection.
7. Add a resume-layout layer that can render grouped roles, projects, and target-specific summaries instead of one flat accomplishment list.

## Outcome

The repo is already strong enough to support a real candidate-profile workflow and to validate final resume artifacts. It is not yet strong enough to fully automate high-quality targeted resume authoring without manual editing.

This task showed that the right next step is not "start from scratch." It is to build on the existing profile normalization and ATS-check pipeline by adding better structured resume inputs and a stronger renderer.
