---
status: pending
priority: p1
issue_id: "002"
tags: [code-review, security]
dependencies: []
---

# Implement .gitignore PII protection before any feature work

## Problem Statement

Generated resumes, answer sets, and company research files contain PII (candidate name, email, phone, salary, work authorization). The `.gitignore` does NOT exclude any data directories. A single `git add .` or accidental push would expose everything. This was confirmed by reading the actual `.gitignore` file.

## Findings

- Current `.gitignore` has only Python/IDE patterns -- zero data directory exclusions
- `profile/raw/preferences.md` already contains real salary data ($140,000) as an untracked file
- `profile/normalized/candidate-profile.json` contains real email and phone
- The plan's security hardening section identifies this correctly but it is NOT yet implemented
- Security review found additional gaps: `docs/reports/*-report.md` and `examples/results/` also need exclusion
- `*.tmp` files should be excluded as defense-in-depth for the atomic write pattern

## Proposed Solutions

### Option 1: Implement immediately as isolated commit (Recommended)

**Approach:** Add exclusions to `.gitignore` and commit as a standalone change before any other work.

**Lines to add:**
```
# Candidate PII and generated artifacts
profile/raw/
profile/normalized/
data/generated/
data/companies/
data/applications/
data/leads/
data/runs/
docs/reports/*-report.md
examples/results/
*.tmp
```

**Effort:** 5 minutes
**Risk:** None -- purely additive

### Option 2: Add pre-commit hook to scan for PII patterns

**Approach:** In addition to `.gitignore`, add a hook that rejects staged files containing email/phone patterns.

**Effort:** 30 minutes
**Risk:** Low but adds maintenance

## Recommended Action

Option 1 immediately. Option 2 as a follow-up.

## Acceptance Criteria

- [ ] `.gitignore` excludes all data directories listed above
- [ ] `.gitignore` commit is the FIRST commit before any feature work
- [ ] `git status` shows profile/ and data/ files as untracked (not staged)

## Work Log

### 2026-04-16 - Discovery

**By:** Security review agent, confirmed by architecture review

**Actions:**
- Read actual `.gitignore` file -- confirmed zero data exclusions
- Identified `docs/reports/` and `examples/results/` as additional gaps not in original plan
- Recommended `*.tmp` exclusion for atomic write defense-in-depth
