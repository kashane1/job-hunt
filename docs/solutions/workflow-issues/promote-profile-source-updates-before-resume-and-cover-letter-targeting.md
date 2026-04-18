---
title: Promote profile-source updates before targeted resume and cover-letter drafting
date: 2026-04-17
module: candidate_materials
problem_type: workflow_issue
component: profile_workflow
symptoms:
  - Resume refreshes were drifting into one-off edits instead of improving the reusable candidate profile
  - New role targets such as AI Engineer and AI Systems Engineer were not consistently reflected across resume, preferences, and answer-bank materials
  - Stale company-specific answers and outdated timeline language remained in raw profile docs and could leak into future applications
  - The built-in resume and cover-letter generators were usable, but manual drafts still outperformed them for high-quality targeting
root_cause: The workflow treated resumes and cover letters as final documents to edit directly instead of outputs generated from a maintained profile source of truth, so new narrative changes were not consistently propagated through the repo
tags:
  - workflow
  - resume
  - cover-letter
  - profile
  - generation
  - answer-bank
severity: medium
---

# Promote profile-source updates before targeted resume and cover-letter drafting

## Problem

The repo already had profile normalization, resume generation, cover-letter generation, ATS checks, and generated artifact storage. But in practice, a real resume refresh still wanted manual intervention because the strongest new information lived in scattered raw docs, GitHub context, and recent work notes rather than in a maintained profile source of truth.

That created two risks:

- targeted resume and cover-letter work became a one-off chat exercise instead of a repeatable repo workflow
- stale answers and outdated role positioning could leak into future applications

## Root Cause

The workflow had the right plumbing but the wrong emphasis. The durable system artifacts already existed, but the candidate-facing source docs had not been updated enough to carry the new story cleanly:

- BAM Global Systems title/timeline updates
- GitHub and side-project signals
- willingness to apply for AI Engineer / AI Systems Engineer / Applied AI Engineer roles
- stronger BAM accomplishments from 2026 work
- cleaned generic answer-bank language

## Solution

Treat the raw profile materials as the real product surface, then generate targeted outputs from there.

### Process changes used in this session

1. Update the reusable source docs first:
   - `profile/raw/candidate-identity.md`
   - `profile/raw/preferences.md`
   - `profile/raw/accomplishments.md`
   - `profile/raw/question-examples.txt`
   - `profile/raw/Kashane Sakhakorn Resume.txt`
2. Add first-class `project_note` inputs for important side projects:
   - `profile/raw/ai-company-os.md`
   - `profile/raw/job-hunt.md`
3. Re-run profile normalization so the repo’s structured profile reflects the new narrative:

```bash
python3 scripts/job_hunt.py normalize-profile
```

4. Create targeted output artifacts only after the profile is current:
   - mid/senior software engineer resume
   - AI engineer resume
   - three reusable cover-letter templates
5. Validate outputs with existing ATS checks instead of treating them as unverified prose.

### Why this worked

- It pushed the narrative change into the reusable profile layer instead of trapping it in one markdown file.
- It aligned preferences, answer-bank content, resumes, and cover letters around the same updated role targets.
- It exposed real system gaps clearly: structured employment history, answer-bank hygiene, richer resume layout, and multi-style cover-letter generation.
- It proved the repo is already strong at storing, normalizing, and validating candidate materials, even if the final generators still need to improve.

## Key repo signal from this session

The repo already supports more repeatable drafting than it first appears:

- `generate-cover-letter` already exists in `src/job_hunt/core.py` and `src/job_hunt/generation.py`
- cover letters can already be unique per job lead
- ATS checks already work for resumes and cover letters
- generated artifacts already have a good JSON + markdown storage shape

The missing step was not “build everything from scratch.” It was “promote the candidate profile into a better source of truth before expecting high-quality generated outputs.”

## Prevention

- When resume or cover-letter quality feels off, update `profile/raw/` first instead of patching only the final generated artifact.
- Treat answer-bank cleanup as required maintenance, especially for stale dates, outdated titles, and company-specific language.
- Add new target roles in `preferences.md` as soon as the search direction changes.
- Add project work as `project_note` docs rather than burying it in long work notes.
- Use the generated JSON records and ATS checks as the validation layer, not as the place where narrative work begins.

## References

- `profile/raw/candidate-identity.md`
- `profile/raw/preferences.md`
- `profile/raw/accomplishments.md`
- `profile/raw/question-examples.txt`
- `profile/raw/Kashane Sakhakorn Resume.txt`
- `profile/raw/ai-company-os.md`
- `profile/raw/job-hunt.md`
- `docs/brainstorms/2026-04-17-resume-workflow-notes.md`
- `docs/brainstorms/2026-04-17-cover-letter-planning-prompt.md`
- `data/generated/resumes/kashane-sakhakorn-mid-senior-software-engineer-2026-04-17.md`
- `data/generated/resumes/kashane-sakhakorn-ai-engineer-2026-04-17.md`
- `data/generated/cover-letters/kashane-platform-internal-tools-template-2026-04-17.md`
- `data/generated/cover-letters/kashane-ai-engineer-template-2026-04-17.md`
- `data/generated/cover-letters/kashane-product-minded-engineer-template-2026-04-17.md`
