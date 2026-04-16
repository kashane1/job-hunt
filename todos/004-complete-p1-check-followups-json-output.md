---
status: pending
priority: p1
issue_id: "004"
tags: [code-review, agent-native]
dependencies: []
---

# Make check-follow-ups output JSON, not human text

## Problem Statement

The plan shows `check-follow-ups` output as human-readable formatted text. An agent cannot reliably parse this. Every other command in the repo writes JSON. This breaks the convention and agent parity.

## Findings

- Plan shows output like: "ExampleCo - Staff Platform Engineer (applied 2026-04-08, 7 days ago)"
- No existing CLI command uses unparseable human text as its primary output
- Same issue applies to `list-applications` and `check-integrity` -- output format unspecified

## Proposed Solutions

### Option 1: Default to JSON stdout for all query commands (Recommended)

**Approach:** All query commands (`check-follow-ups`, `list-applications`, `check-status`, `check-integrity`) output JSON arrays to stdout by default. Add `--format text` flag for human-readable output.

**Effort:** Small per command
**Risk:** None

## Acceptance Criteria

- [ ] `check-follow-ups` outputs JSON array with `lead_id`, `company_name`, `days_since_applied`, `follow_up_type`, `suppressed`
- [ ] `list-applications` outputs JSON array
- [ ] `check-integrity` outputs JSON report with `orphaned`, `dangling`, `missing` arrays
- [ ] All parseable by an agent without string parsing

## Work Log

### 2026-04-16 - Discovery

**By:** Agent-native reviewer
