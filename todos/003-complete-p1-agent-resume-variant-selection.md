---
status: pending
priority: p1
issue_id: "003"
tags: [code-review, agent-native]
dependencies: []
---

# Add CLI mechanism for agent to select resume variant

## Problem Statement

The plan says `build-draft` defaults to "the variant with the highest score" and "Human review can override." But there is no `--select-variant` flag or equivalent. An agent that generates 3 variants has no programmatic way to choose one. This breaks agent-native parity.

## Findings

- `build-draft` currently takes `--lead` and `--profile` but has no variant selection flag
- An agent cannot complete the draft-building step without human intervention to pick a variant
- 10 of 12 proposed capabilities are fully agent-accessible; this is one of two that are not

## Proposed Solutions

### Option 1: Add --resume-variant flag to build-draft (Recommended)

**Approach:** Add `--resume-variant <content-id>` to `build-draft`. If omitted, auto-select by highest relevance score. Document in AGENTS.md.

**Effort:** Small (1 arg + 5 lines of selection logic)
**Risk:** Low

## Acceptance Criteria

- [ ] `build-draft --resume-variant <id>` selects the specified variant
- [ ] Omitting the flag auto-selects the highest-scoring variant
- [ ] AGENTS.md documents the flag for agent use

## Work Log

### 2026-04-16 - Discovery

**By:** Agent-native reviewer
