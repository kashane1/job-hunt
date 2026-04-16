---
title: Harden profile normalization so candidate signals beat noisy raw-document artifacts
date: 2026-04-16
module: profile_normalization
problem_type: workflow_issue
component: profile_ingestion
symptoms:
  - Candidate contact fields were polluted by arbitrary emails, dates, IDs, localhost URLs, and job links pulled from work notes and draft dumps
  - Mixed raw documents like `Drafts.txt` were misclassified and failed to yield a useful answer bank
  - Skills were under-extracted for the real stack and also over-extracted through loose substring matching
  - Preferences stayed empty even when grounded answers clearly stated remote preference, target role, compensation, and search timeline
root_cause: The first-pass normalizer treated all raw text as equally trustworthy, used broad regex extraction without document-type trust boundaries, and relied on narrow keyword matching instead of candidate-signal-aware parsing
tags:
  - workflow
  - normalization
  - profile
  - ingestion
  - scoring
  - answer-bank
severity: high
---

# Harden profile normalization so candidate signals beat noisy raw-document artifacts

## Problem

The profile normalization pipeline produced structurally valid artifacts, but several fields were low-trust and operationally misleading. Candidate contact data was contaminated by internal work-note content, the answer bank missed obvious application responses in freeform draft files, and preferences stayed blank even when the raw documents already contained grounded answers.

## Root Cause

The process over-weighted generic text extraction and under-weighted source trust. In practice, that caused three failures:

- contact extraction ran on documents that should never contribute identity fields
- freeform application answers were only captured when written in rigid `Q:` / `A:` form
- skill and preference extraction depended on narrow or overly loose heuristics rather than bounded candidate-oriented signals

## Solution

Tighten normalization around trust boundaries and second-pass inference rather than patching generated outputs by hand.

### Process changes

1. Restrict contact extraction to candidate-facing document types such as resumes, cover letters, preferences, and question banks.
2. Read contact fields only from the top portion of trusted documents and normalize phones to a strict 10-digit US format.
3. Keep only personal profile links such as LinkedIn instead of importing every URL found in raw notes.
4. Infer better document titles from stable filenames when headings are generic or noisy.
5. Extract answer-bank entries from freeform prompt/answer blocks inside candidate-facing docs, not just explicit `Q:` / `A:` pairs.
6. Expand skill extraction to the actual stack in the raw materials using alias-aware, boundary-safe matching.
7. Add a second pass that infers preferences from grounded application answers, including remote preference, preferred location, compensation target, search timeline, and target role signal.

### Key implementation pattern

```python
if document_type not in CONTACT_DOC_TYPES:
    return {"emails": [], "phones": [], "links": []}

header_text = "\n".join(meaningful_lines(text, limit=8))
```

```python
if document_type in {"cover_letter", "question_bank"}:
    for item in extract_prompt_answer_pairs(text, document_id):
        ...
```

```python
for skill, patterns in SKILL_ALIAS_PATTERNS.items():
    if any(pattern.search(lowered_text) for pattern in patterns):
        hits.add(skill)
```

## Why this worked

- It moved the fix into the repeatable process instead of making one-off corrections to generated JSON.
- It preserved strictness by only inferring preferences from grounded candidate-authored answers.
- It reduced high-noise extraction from work notes while improving useful signal capture from mixed draft files.
- It made the normalized profile more actionable for downstream lead scoring and draft generation.

## Prevention

- Treat raw profile docs as trust-tiered inputs rather than one undifferentiated text pool.
- Only let high-trust document types populate identity fields.
- Support both structured and semi-structured answer-bank formats because real candidate notes are rarely clean.
- Prefer boundary-aware token and alias matching for skills over raw substring checks.
- Add regression tests for every normalization bug found in real raw documents before rerunning the pipeline.

## References

- `src/job_hunt/core.py`
- `tests/test_pipeline.py`
- `profile/raw/Drafts.txt`
- `profile/raw/Kashane Sakhakorn Resume.txt`
- `profile/raw/Work Notes 2025.txt`
