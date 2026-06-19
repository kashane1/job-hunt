# Resume variants

Drop your ATS-passing resume variants here, one markdown file per job-title
lane. The resume variant registry (`config/resume-variants.json`) routes each
job to the best lane and uses the file named in that lane's `resume_path`.

Expected files (paths referenced by the shipped registry):

| Lane id            | File                                | Use for |
|--------------------|-------------------------------------|---------|
| `ai_engineer`      | `profile/resumes/ai-engineer.md`    | AI / ML / LLM roles |
| `platform_backend` | `profile/resumes/platform-backend.md` | platform / backend / infra / SRE |
| `fullstack_product`| `profile/resumes/fullstack-product.md` | full-stack / product / frontend |
| `generalist_swe`   | (defaults to the bundled example resume) | catch-all |

Until a lane's file exists, `select-resume-variant` still routes to it but flags
the decision `needs_human_review` with `resume_source_missing` so you know to
author it. Replace the default lane's `resume_path` with your own generalist
resume once you have one.

These files are gitignored by default if they contain real personal data —
check `.gitignore` before committing.
