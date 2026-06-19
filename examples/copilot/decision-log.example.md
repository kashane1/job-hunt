# Co-Pilot Run Decision Log

- Generated: 2026-06-19T01:07:37+00:00
- Window: leads since `1h` (>= 2026-06-19T00:07:37.468935+00:00)
- Min tier: `no`
- In window: 5 (strong_yes=0, maybe=0, no=5, unscored=0)
- Jobs planned: 5 (5 need review)

> **Human gate:** Co-pilot prepares applications up to a filled-but-unsubmitted form. Final Submit always requires a human click (auto_submit_tiers = []).

## 1. IT Systems Engineer, Client Platform Engineer — Anthropic

- Lead: `anthropic-it-systems-engineer-client-platform-engineer-edd1371c`
- Fit: **no** (score 27)
- Why matched: Matched skills: platform. Title alignment: limited. Location: no-location-preference-set. Penalty keywords: none.
- Resume variant: **platform_backend** (confidence high, score 72.5)
  - Routed to 'platform_backend' (score 72.5): title matched ['platform engineer']; emphasis skills overlap ['platform']; inferred seniority mid.
  - Resume file: `profile/resumes/platform-backend.md` (exists: False)
- ⚠️ Needs human review: resume_source_missing:profile/resumes/platform-backend.md
- Next steps:
  - `python3 scripts/job_hunt.py select-resume-variant --lead data/leads/anthropic-it-systems-engineer-client-platform-engineer-edd1371c.json`
  - `python3 scripts/job_hunt.py prepare-application --lead data/leads/anthropic-it-systems-engineer-client-platform-engineer-edd1371c.json`
  - `python3 scripts/job_hunt.py apply-posting --draft-id <draft-id-from-prepare>`

## 2. Applied AI Engineer — Anthropic

- Lead: `anthropic-applied-ai-engineer-09b33c17`
- Fit: **no** (score 11)
- Why matched: Matched skills: none. Title alignment: limited. Location: no-location-preference-set. Penalty keywords: none.
- Resume variant: **ai_engineer** (confidence high, score 78.75)
  - Routed to 'ai_engineer' (score 78.75): title matched ['ai engineer', 'applied ai']; emphasis skills overlap ['ai']; inferred seniority mid.
  - Resume file: `profile/resumes/ai-engineer.md` (exists: False)
- ⚠️ Needs human review: resume_source_missing:profile/resumes/ai-engineer.md
- Next steps:
  - `python3 scripts/job_hunt.py select-resume-variant --lead data/leads/anthropic-applied-ai-engineer-09b33c17.json`
  - `python3 scripts/job_hunt.py prepare-application --lead data/leads/anthropic-applied-ai-engineer-09b33c17.json`
  - `python3 scripts/job_hunt.py apply-posting --draft-id <draft-id-from-prepare>`

## 3. Data Engineer — Anthropic

- Lead: `anthropic-data-engineer-fd69d0c0`
- Fit: **no** (score 42)
- Why matched: Matched skills: data. Title alignment: limited. Location: no-location-preference-set. Penalty keywords: none.
- Resume variant: **generalist_swe** (confidence low, score 10.0, fallback)
  - No specialized lane cleared the 22.0 threshold; used default lane 'generalist_swe'. Inferred seniority: mid.
  - Resume file: `examples/profile/raw/resume.md` (exists: True)
- ⚠️ Needs human review: no_specialized_lane_cleared_threshold (best=ai_engineer@10.0)
- Next steps:
  - `python3 scripts/job_hunt.py select-resume-variant --lead data/leads/anthropic-data-engineer-fd69d0c0.json`
  - `python3 scripts/job_hunt.py prepare-application --lead data/leads/anthropic-data-engineer-fd69d0c0.json`
  - `python3 scripts/job_hunt.py apply-posting --draft-id <draft-id-from-prepare>`

## 4. Full-Stack Software Engineer, Reinforcement Learning — Anthropic

- Lead: `anthropic-full-stack-software-engineer-reinforcement-learning-d1d54353`
- Fit: **no** (score 42)
- Why matched: Matched skills: data. Title alignment: limited. Location: no-location-preference-set. Penalty keywords: none.
- Resume variant: **fullstack_product** (confidence high, score 70.0)
  - Routed to 'fullstack_product' (score 70.0): title matched ['full-stack']; inferred seniority mid.
  - Resume file: `profile/resumes/fullstack-product.md` (exists: False)
- ⚠️ Needs human review: resume_source_missing:profile/resumes/fullstack-product.md
- Next steps:
  - `python3 scripts/job_hunt.py select-resume-variant --lead data/leads/anthropic-full-stack-software-engineer-reinforcement-learning-d1d54353.json`
  - `python3 scripts/job_hunt.py prepare-application --lead data/leads/anthropic-full-stack-software-engineer-reinforcement-learning-d1d54353.json`
  - `python3 scripts/job_hunt.py apply-posting --draft-id <draft-id-from-prepare>`

## 5. ML Infrastructure Engineer, Safeguards — Anthropic

- Lead: `anthropic-ml-infrastructure-engineer-safeguards-1a67a0f3`
- Fit: **no** (score 46)
- Why matched: Matched skills: data. Title alignment: limited. Location: no-location-preference-set. Penalty keywords: none.
- Resume variant: **platform_backend** (confidence high, score 72.5)
  - Routed to 'platform_backend' (score 72.5): title matched ['infrastructure engineer']; emphasis skills overlap ['infrastructure']; inferred seniority mid.
  - Resume file: `profile/resumes/platform-backend.md` (exists: False)
- ⚠️ Needs human review: resume_source_missing:profile/resumes/platform-backend.md
- Next steps:
  - `python3 scripts/job_hunt.py select-resume-variant --lead data/leads/anthropic-ml-infrastructure-engineer-safeguards-1a67a0f3.json`
  - `python3 scripts/job_hunt.py prepare-application --lead data/leads/anthropic-ml-infrastructure-engineer-safeguards-1a67a0f3.json`
  - `python3 scripts/job_hunt.py apply-posting --draft-id <draft-id-from-prepare>`

