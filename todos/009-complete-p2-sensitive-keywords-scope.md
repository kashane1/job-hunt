---
status: pending
priority: p2
issue_id: "009"
tags: [code-review, security]
dependencies: []
---

# Clarify SENSITIVE_KEYWORDS scope -- it does NOT protect generated content PII

## Problem Statement

The plan's security hardening proposes expanding `SENSITIVE_KEYWORDS` to cover employment PII terms. But `redact_sensitive_data` works by matching JSON KEY NAMES, not values. The candidate's email is under `contact.emails` (key "emails" does not match), salary is in answer text under key "answer" (does not match). The expansion gives a false sense of PII protection.

## Findings

- `redact_sensitive_data` only inspects dict keys, not values
- Adding "salary" to SENSITIVE_KEYWORDS would match key `minimum_compensation` (contains "compensation") but NOT the text "$140,000" inside an answer body
- Real PII protection for generated artifacts comes from `.gitignore`, not from field-level redaction
- Follow-up drafts receive full PII dicts (candidate_profile with email, phone, salary) without narrowing

## Proposed Solutions

### Option 1: Document the scope clearly (Recommended)

**Approach:**
- Add "salary" and "compensation" to SENSITIVE_KEYWORDS for browser attempt payload redaction only
- Document explicitly that PII protection for generated artifacts relies on .gitignore exclusion and directory separation, NOT field-level redaction
- Narrow follow-up draft input to only the fields it needs (candidate name, role title, company name, matched skills)

**Effort:** Small
**Risk:** Low

## Acceptance Criteria

- [ ] Plan clarifies SENSITIVE_KEYWORDS scope is browser payloads only
- [ ] Follow-up draft function accepts narrowed input, not full profile dict
- [ ] .gitignore is the primary PII protection mechanism

## Work Log

### 2026-04-16 - Discovery

**By:** Security review agent
