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

## Authoring a lane

Start from the scaffold in `profile/resumes/templates/<lane>.template.md`:

```bash
cp profile/resumes/templates/ai-engineer.template.md profile/resumes/ai-engineer.md
# fill it in with true, evidence-backed content (claims from profile/claims/)
python3 scripts/job_hunt.py profile-doctor
```

## Privacy (enforced by .gitignore)

Real lane files (`profile/resumes/*.md`) are **gitignored** — only this README
and the `templates/` scaffolds are tracked. The claims truth bank under
`profile/claims/` follows the same pattern. See
[`docs/guides/profile-and-resume-privacy.md`](../../docs/guides/profile-and-resume-privacy.md)
for the full policy and the broader title-taxonomy → lane mapping.
