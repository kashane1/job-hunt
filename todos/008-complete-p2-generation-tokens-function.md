---
status: pending
priority: p2
issue_id: "008"
tags: [code-review, python, scoring]
dependencies: []
---

# Create generation_tokens() that preserves short terms like AI, ML, Go

## Problem Statement

The existing `tokens()` function uses `r"[a-z0-9+#.-]{3,}"` which drops 2-character terms. For resume generation where "AI", "ML", "Go", "UI", "CI", "CD", "QA" are legitimate skills, this is a scoring defect. The plan acknowledges this but does not define the fix.

## Findings

- Python review confirmed the gap affects accomplishment relevance scoring
- If generation uses a modified tokenizer but scoring uses the original, Jaccard similarity becomes asymmetric
- Both sides of any Jaccard comparison must use the same tokenizer

## Proposed Solutions

### Option 1: generation_tokens() with 2-char minimum (Recommended)

```python
def generation_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.-]{2,}", text.lower())
```

Use consistently for both sides of all Jaccard comparisons in generation.py. Do not modify core.tokens().

**Effort:** Small
**Risk:** Low

## Acceptance Criteria

- [ ] `generation_tokens()` defined in generation.py
- [ ] Preserves "ai", "ml", "go", "ui", "ci", "cd", "qa"
- [ ] Used consistently on both sides of all Jaccard comparisons
- [ ] Jaccard guarded against empty union: `return 0.0 if not union`

## Work Log

### 2026-04-16 - Discovery

**By:** Python reviewer, architecture reviewer
