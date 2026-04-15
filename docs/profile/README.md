# Profile Docs

Put candidate source documents in `profile/raw/`.

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

