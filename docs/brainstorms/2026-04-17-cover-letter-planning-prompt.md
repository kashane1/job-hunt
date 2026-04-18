# Cover Letter Planning Prompt

Use this prompt in a separate Codex session with `workflows:plan`.

```text
I want you to use workflows:plan to plan improvements for cover-letter generation in the `job-hunt` repo at `/Users/simons/job-hunt`.

Context:
- The repo already has `generate-cover-letter` support in `src/job_hunt/core.py` and `src/job_hunt/generation.py`.
- It can generate per-job cover letters from a lead + normalized candidate profile + optional company research.
- It also has ATS checks for cover letters in `src/job_hunt/ats_check.py`.
- However, the current cover-letter generation is too formulaic and only has one default style.

Recent manual work:
- We created three stronger manual cover-letter templates for reuse:
  - `/Users/simons/job-hunt/data/generated/cover-letters/kashane-platform-internal-tools-template-2026-04-17.md`
  - `/Users/simons/job-hunt/data/generated/cover-letters/kashane-ai-engineer-template-2026-04-17.md`
  - `/Users/simons/job-hunt/data/generated/cover-letters/kashane-product-minded-engineer-template-2026-04-17.md`
- Matching content-record JSON files live beside them in the same folder.
- These templates reflect three distinct strengths/lanes:
  - backend/platform/internal tools
  - AI engineer / AI systems
  - product-minded software engineer / internal apps

Relevant raw profile inputs:
- `/Users/simons/job-hunt/profile/raw/Kashane Sakhakorn Resume.txt`
- `/Users/simons/job-hunt/profile/raw/accomplishments.md`
- `/Users/simons/job-hunt/profile/raw/question-examples.txt`
- `/Users/simons/job-hunt/profile/raw/preferences.md`
- `/Users/simons/job-hunt/profile/raw/ai-company-os.md`
- `/Users/simons/job-hunt/profile/raw/job-hunt.md`

What I want from the plan:
1. Assess exactly what parts of the current cover-letter process are already repeatable in the repo.
2. Identify the main weaknesses in the existing generator and data model.
3. Propose a path to support multiple cover-letter styles or “strength lanes” instead of one generic template.
4. Propose how to make cover letters more job-specific without fabricating company facts.
5. Propose how to reuse candidate raw materials and question-bank content safely, including guardrails against stale company-specific language.
6. Recommend a minimal first implementation slice and a later higher-quality slice.
7. Include files likely to change, test strategy, and risks.

Constraints:
- Do not implement yet. This is planning only.
- Prefer building on the existing pipeline rather than inventing a parallel system.
- Treat the manual cover-letter templates as product-quality examples to learn from.
- Be explicit about what should remain grounded versus synthesized.

Output:
- A practical implementation plan that could be handed to another Codex instance for execution.
```
