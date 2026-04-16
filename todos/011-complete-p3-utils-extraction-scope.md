---
status: pending
priority: p3
issue_id: "011"
tags: [code-review, architecture]
dependencies: []
---

# Define utils.py extraction scope and boundaries

## Problem Statement

The plan lists 9 functions to extract into utils.py but does not clarify the boundary between utils and domain logic. The modified tokenizer for generation should NOT go in utils.py. Regex constants should stay in their domain modules.

## Proposed Solutions

### Option 1: Pure utilities only

**Rule:** `utils.py` contains pure utility functions with zero domain knowledge: `write_json`, `read_json`, `slugify`, `short_hash`, `now_iso`, `ensure_dir`, `unique_preserve_order`, `load_yaml_file`, `tokens`, `display_path`, `parse_frontmatter`, `meaningful_lines`. Domain-specific constants and regex patterns stay in their respective modules. `generation_tokens()` lives in `generation.py`.

**Effort:** Small (documentation + extraction)
**Risk:** Low

## Acceptance Criteria

- [ ] utils.py boundary documented in plan
- [ ] No domain-specific logic in utils.py
- [ ] core.py re-exports for backward compatibility

## Work Log

### 2026-04-16 - Discovery

**By:** Architecture reviewer, Python reviewer
