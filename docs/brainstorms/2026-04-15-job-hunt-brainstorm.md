---
date: 2026-04-15
topic: job-hunt
---

# Job Hunt Brainstorm

## What We're Building

`job-hunt` should be an agent-first repo for finding, ranking, and optionally applying to jobs on behalf of one specific person. The repo's main asset is not a web app. It is a trustworthy operating system for a job-search agent: profile context, scoring rules, runbooks, application reports, and durable records of what was considered, what was submitted, and how confidently the agent acted.

The product should work in phases. First, collect and score openings. Second, optionally let a human choose what to apply to. Third, execute applications through job boards and company sites using browser automation. Fourth, generate a detailed audit trail for every submission, including what facts came directly from source documents, what was inferred, and where the agent lacked enough evidence.

## Why This Approach

Three viable shapes emerged:

### Approach A: Full Auto-Apply From Day One

The system searches, scores, applies, creates accounts when needed, and submits with minimal review.

**Pros:**
- Maximum automation
- Fastest path to high application volume

**Cons:**
- Highest risk of bad answers, repeated mistakes, and account issues
- Harder to trust early because every weak assumption becomes user-visible

**Best when:** the profile is already highly structured and the user is comfortable with aggressive autonomy

### Approach B: Search/Score First, Apply Second

The system separates lead generation from application execution. It first builds a ranked queue with evidence and fit reports, then applies only after a runtime rule or human approval step.

**Pros:**
- Much safer and easier to trust
- Makes scoring, calibration, and profile gaps visible before submissions happen
- Lets us improve application quality without losing collection velocity

**Cons:**
- Slightly more process
- Requires explicit queue and state management

**Best when:** trust, auditability, and controlled rollout matter

### Approach C: CRM Only

The repo stores jobs, notes, and statuses, but the application step is mostly manual.

**Pros:**
- Very low implementation risk
- Good for organization right away

**Cons:**
- Misses the main automation opportunity
- Does not leverage browser-driven agent execution

**Best when:** the goal is tracking only, not agentic application execution

**Recommendation:** Start with Approach B. It matches your idea of a collection phase plus optional human review, and it gives us a safer path to full autonomy later.

## Key Decisions

- Separate pipeline stages:
  `discover -> dedupe -> enrich -> score -> review -> apply -> report -> learn`
- Treat profile documents as source truth:
  resumes, cover letters, work history notes, question banks, and preferences should feed the agent, but unsupported facts should be flagged rather than silently invented.
- Default policy should be "do not fabricate":
  if an application asks for something unsupported, the system should either leave it blank, mark it for review, or explicitly record that a speculative answer was used.
- Use two independent scores:
  one for job fit and one for application answer quality.
- Keep a provenance trail for answers:
  every important answer should say whether it came from a source doc, a synthesis of multiple docs, a weak inference, or a fabricated value.
- Build this repo as a job-hunt operating repo, not a giant generic framework:
  borrow ECC patterns for skills, hooks, memory, and evals, but do not copy the whole repo surface.

## Scoring Model

### 1. Job Fit Score

Measures how good the role is for the user.

Suggested factors:
- title match
- skills match
- seniority match
- domain/industry match
- compensation match if available
- location/remote match
- visa/work authorization compatibility if relevant
- required experience gaps
- negative signals like relocation, clearance, or stack mismatch

Suggested output:
- `fit_score`: 0-100
- `fit_recommendation`: strong yes / maybe / no
- `fit_rationale`: short evidence-backed explanation

### 2. Application Quality Score

Measures how well the agent completed the application.

Suggested factors:
- percent of answers grounded in profile docs
- percent inferred from strong evidence
- percent inferred from weak evidence
- percent fabricated or user-supplied at runtime
- number of unanswered or low-confidence questions
- number of custom free-text answers generated
- resume / cover letter suitability for this role

Suggested output:
- `application_quality_score`: 0-100
- `confidence_band`: high / medium / low
- `truthfulness_rating`: strict / inferred / fabricated
- `needs_human_review`: yes / no

## Honesty Policy

This part is important enough to make explicit.

The agent should not have runtime freedom to "make up facts" by default. That is a tempting shortcut, but it creates reputational risk and breaks trust in the report. A better policy is:

- `strict`: only use supported facts from the profile corpus
- `inferred`: allow clearly labeled synthesis when evidence is strong
- `speculative`: allowed only if you explicitly opt in at runtime

If `speculative` mode is ever used, the report should say exactly which answers were speculative and why.

## Main Product Capabilities

### Profile Intelligence

The repo should ingest and normalize:
- resumes
- cover letters
- project/work notes
- accomplishments
- skills inventory
- answer bank for common application questions
- job search preferences and deal-breakers

This should produce a structured profile layer the agent can query quickly instead of repeatedly rereading raw docs.

### Job Discovery

The system should support two families of sources:
- job boards where the user is already signed in
- company career sites

Discovery output should be normalized into a single lead format so scoring and tracking do not care where the job came from.

### Application Execution

The browser agent should:
- navigate application flows
- upload the right resume/cover letter variant
- answer form questions
- create accounts only when needed
- save screenshots or raw notes for critical steps
- stop safely when confidence is too low

### Reporting and Audit

Each submitted application should produce both:
- a machine-readable record for analysis
- a human-readable report for trust and review

## Risks And Critique

- Storing passwords in reports is a bad idea:
  credentials should live in local secrets or a password manager, not in git-tracked artifacts. Reports can record that an account was created and which email was used, but not the password itself.
- Anti-bot and site variability will be the hardest execution problem:
  many company sites look similar but differ in subtle ways. We should expect a generic browser strategy plus site-specific fallback playbooks.
- Raw documents alone are not enough:
  if the corpus stays unstructured, answer quality will drift. We should add a normalized profile layer early.
- Reports can become too verbose to be useful:
  the right pattern is structured data first, markdown narrative second.
- Full autonomy on day one is likely too risky:
  better to prove discovery, scoring, and reporting before enabling mass auto-apply.

## Recommended Repo Shape

Keep the first version small and domain-specific:

```text
job-hunt/
├── AGENTS.md
├── README.md
├── docs/
│   ├── brainstorms/
│   ├── profile/
│   └── reports/
├── profile/
│   ├── raw/
│   ├── normalized/
│   └── preferences/
├── data/
│   ├── leads/
│   ├── applications/
│   ├── companies/
│   └── runs/
├── schemas/
│   ├── lead.schema.json
│   ├── application.schema.json
│   ├── profile.schema.json
│   └── report.schema.json
├── playbooks/
│   ├── discovery/
│   ├── application/
│   └── fallback/
├── prompts/
│   ├── scoring/
│   ├── answering/
│   └── reporting/
├── scripts/
│   ├── normalize_profile.py
│   ├── score_leads.py
│   └── summarize_run.py
└── jobs/
    ├── queue/
    ├── shortlisted/
    └── applied/
```

## Record Design

### Lead Record

Every discovered job should include:
- source URL
- company
- title
- location
- compensation if present
- raw description
- normalized requirements
- dedupe fingerprint
- fit score
- status: discovered / shortlisted / skipped / applied

### Application Record

Every application should include:
- lead ID
- application URL
- date/time
- submitted status
- assets used: resume, cover letter
- question and answer log
- provenance per answer
- blockers encountered
- screenshots or browser notes
- application quality score

### Run Summary

Every execution session should include:
- search criteria used
- sources visited
- number of leads found
- number shortlisted
- number attempted
- number successfully submitted
- failures and causes
- tokens/time if useful for cost tracking

## What To Reuse From ECC

Use ECC as a pattern library, not a base product.

Strong candidates to borrow conceptually:
- project-specific `AGENTS.md` guidance
- skills as reusable workflows
- memory and session-summary patterns
- eval and quality-gate ideas
- hook-based automation where helpful
- research-first workflow before acting

Things not worth copying blindly:
- the full multi-agent catalog
- cross-editor/plugin packaging
- broad language/framework rules unrelated to this repo
- generic marketplace/install surface

## Suggested Phased Rollout

### Phase 1: Trustworthy Discovery
- ingest profile docs
- normalize profile data
- discover and dedupe leads
- score and rank opportunities
- generate shortlist reports

### Phase 2: Assisted Applications
- support human selection of leads
- fill applications in browser
- stop when unsupported questions appear
- generate detailed per-application reports

### Phase 3: Controlled Autonomy
- apply automatically to top `X` jobs under explicit runtime rules
- allow stricter or looser answer policies
- add retry/fallback playbooks by site type

### Phase 4: Learning Loop
- measure which jobs got interviews
- refine scoring weights
- identify missing profile evidence
- improve answer-bank coverage over time

## Open Questions

- Should the first implementation be mostly markdown plus scripts, or a more formal app with a database from the start?
- Do you want default operation to require review before submit, or only for low-confidence applications?
- Which job boards matter most for v1?
- Should company-account creation be fully automatic, or only after a prompt/approval checkpoint?

## Next Steps

1. Decide the repo shape:
   agent-first repo with files and scripts is my recommendation for v1
2. Decide the trust model:
   I recommend `strict` and `inferred` modes first, with no speculative facts
3. Turn this brainstorm into an implementation plan:
   define directories, schemas, prompts, and the first end-to-end workflow
