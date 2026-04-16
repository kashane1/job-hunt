# Profile Docs

Put candidate source documents in `profile/raw/`.

V1 normalization supports markdown and PDF inputs. Keep DOCX sources out of the main workflow until DOCX ingestion is added intentionally.

Recommended document types:
- `resume`
- `cover_letter`
- `work_note`
- `question_bank`
- `preferences`

Documents are easiest to normalize when they include YAML frontmatter:

```yaml
---
document_type: preferences
title: Candidate Preferences
target_titles:
  - Staff Platform Engineer
  - Senior Backend Engineer
preferred_locations:
  - Remote
remote_preference: remote
excluded_keywords:
  - clearance
  - relocation required
---
```

Question-bank documents should use `Q:` and `A:` pairs in the body.

Repeatable intake flow:

```bash
python3 scripts/job_hunt.py normalize-profile
```

The normalization pass also audits every raw file for:
- `quality`: structure, specificity, and grounded signals
- `quantity`: how much reusable material the file contains
- `value`: how useful the file is for downstream job-search drafting

Outputs:
- `profile/normalized/documents/*.json` for per-document normalized records
- `profile/normalized/document-audit.json` for machine-readable scoring
- `docs/reports/profile-document-audit.md` for a human-readable review
