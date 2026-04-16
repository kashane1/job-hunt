---
status: pending
priority: p2
issue_id: "006"
tags: [code-review, data-integrity, performance]
dependencies: []
---

# Harden write_json atomicity and extend to paired .md files

## Problem Statement

The plan proposes atomic `write_json` via write-to-temp-then-rename but: (1) no try/finally cleanup of temp files on failure, (2) paired .md content files are not covered by atomic write, (3) should use `os.replace()` not `Path.rename()` for cross-platform safety.

## Findings

- Python review provided improved pattern with try/finally
- Performance review noted paired .json + .md writes create a consistency window
- If process interrupted between JSON and MD write, orphaned half-pair results
- `Path.rename()` fails on Windows if destination exists; `os.replace()` is atomic on all platforms

## Proposed Solutions

### Option 1: Atomic write helper with try/finally + paired write utility

```python
def write_json_atomic(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

def write_paired_content(json_path: Path, json_payload: dict, md_path: Path, md_content: str) -> None:
    # Write both to .tmp, then rename both
    ...
```

**Effort:** Small
**Risk:** Low

## Acceptance Criteria

- [ ] `write_json` uses os.replace() with try/finally cleanup
- [ ] Generated content writes both .json and .md atomically
- [ ] Interrupted writes leave no orphaned .tmp files

## Work Log

### 2026-04-16 - Discovery

**By:** Python reviewer, performance reviewer, data integrity reviewer
