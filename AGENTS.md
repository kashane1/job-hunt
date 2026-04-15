# AGENTS.md

## Mission

Operate this repository as a trustworthy job-search system for one person. The goal is not maximum submission volume. The goal is high-quality discovery, honest application drafting, safe browser execution, and durable audit trails.

## Core Policies

- Default to `strict` answer policy.
- Use supported facts from the candidate profile whenever possible.
- Inference is allowed only when the output is clearly labeled.
- Do not fabricate unsupported facts unless runtime policy explicitly allows it.
- V1 requires explicit human approval before every final submit.
- Never store passwords or secrets in git-tracked files.

## Browser Guardrails

- Soft tab limit: 10
- Hard tab limit: 15
- Reuse the current tab whenever possible.
- Close background tabs aggressively before opening new ones.
- If the hard limit is reached, stop safely and record the failure.

## Artifact Expectations

- `profile/normalized/` stores machine-readable profile context.
- `data/leads/` stores normalized leads and scoring output.
- `data/applications/` stores application drafts and JSON reports.
- `docs/reports/` stores human-readable markdown reports.
- `data/runs/` stores run summaries.

## Reporting Requirements

Every application attempt must record:
- whether approval was required
- whether approval was obtained
- what answers were used
- provenance for each answer
- confidence level
- blockers encountered
- browser tab metrics
- whether the submission was confirmed

## Document Conventions

Profile documents work best with YAML frontmatter such as:

```yaml
---
document_type: resume
title: Senior Platform Resume
tags:
  - python
  - platform
  - backend
---
```

## Safety Overrides

If runtime configuration conflicts with these defaults, prefer the stricter option unless the user explicitly asked for looser behavior in the current session.

