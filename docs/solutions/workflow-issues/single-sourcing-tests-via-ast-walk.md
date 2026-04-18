---
title: "Single-sourcing invariant tests survive constant relocation via AST walk"
category: workflow-issues
tags: [testing, invariants, refactoring, python, ast]
module: any
symptom: "Renaming or relocating a module constant breaks an unrelated test that grep-matched the old value"
root_cause: "Grep-based 'string X appears exactly once' tests are brittle under refactoring"
severity: medium
date: 2026-04-18
---

# Single-sourcing invariant tests survive constant relocation via AST walk

## Problem

A test enforces "this literal value must appear exactly once across the
codebase" — the invariant is useful (prevents duplicated magic strings),
but the test is implemented as a substring grep over a single file. Any
refactor that relocates the constant to another module — or replaces it
with a different literal — breaks the test even though the invariant
still holds.

Concrete example in this repo: `tests/test_discovery.py:DiscoveryUserAgentTest`
greps `src/job_hunt/discovery.py` for `"job-hunt/"`. When Phase 2 of the
discovery-hardening plan moved `DISCOVERY_USER_AGENT` to `net_policy.py`
and swapped the value for a Chrome UA, the test broke:

```
AssertionError: 0 != 1 : []
```

It reported zero occurrences in the OLD file — correct, because the
constant had moved — but the invariant (*exactly one literal definition
of the UA value*) was still satisfied; the test was just looking in the
wrong place.

## Failed approaches

1. **Expand the substring grep to two files.** Couples the test to
   implementation detail (which file owns the constant). Breaks again
   the next time the constant moves.
2. **Grep for the NEW literal instead of the old.** Pins the test to a
   specific UA string; breaks on any future UA swap.
3. **Whole-file substring check for `DISCOVERY_USER_AGENT` (the runtime
   value).** Fails on multi-line implicit concatenation like:
   ```python
   DISCOVERY_USER_AGENT: Final = (
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
   )
   ```
   At parse time this is one string constant, but in the source file the
   value is split across three lines. Substring search against the
   concatenated runtime value finds zero matches.

## Solution

Walk the AST of every `src/` `.py` file and count `ast.Constant` nodes
whose value equals the runtime value of the constant. This handles:

- **Implicit string concatenation** — Python collapses adjacent literals
  to a single `ast.Constant` at parse time, so `"A " "B"` is indexed as
  one node matching `"A B"`.
- **Relocation** — the test imports the constant by name, not location,
  so it follows the constant wherever it lives.
- **Value swap** — the test always tracks whatever the constant is
  currently defined as; you don't hard-code the expected literal.

```python
import ast
from job_hunt.discovery import DISCOVERY_USER_AGENT

src_root = ROOT / "src" / "job_hunt"
hits: list[str] = []
for py_file in src_root.rglob("*.py"):
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == DISCOVERY_USER_AGENT:
            hits.append(f"{py_file.relative_to(src_root)}:{node.lineno}")
self.assertEqual(
    len(hits), 1,
    f"Expected exactly one literal defining DISCOVERY_USER_AGENT, got: {hits}",
)
```

### Key technique: ship the test change BEFORE the value change

When the WIP already contains both the test-breaking refactor AND the
new value, land the test-only retarget first so CI stays green:

```bash
# 1. Save the producer-side WIP for the touched files
git diff HEAD -- src/module_a.py src/module_b.py > /tmp/producer.patch

# 2. Revert those files to HEAD (keeps the new test modification in place)
git checkout HEAD -- src/module_a.py src/module_b.py

# 3. Verify the new test passes against the un-refactored code
python3 -m unittest tests.test_...

# 4. Commit the test-only retarget
git add tests/test_...
git commit -m "test: retarget invariant to AST walk"

# 5. Reapply the producer changes — next phase will land them cleanly
git apply /tmp/producer.patch
```

This is the tolerant-consumer pattern applied to test invariants:
consumer (the test) tolerates the refactor before the producer (the
code change) flips the value.

## Prevention

- **Whenever a test enforces "exactly N occurrences of literal X,"** reach
  for `ast.walk` + `ast.Constant` rather than `str.splitlines()` + `in`.
- **Import the constant at test time** instead of hard-coding the value
  in the assertion — the test then reflects whatever the current value
  is, not a snapshot from when the test was written.
- **Plan phases explicitly** so test-only changes can precede
  value-changing refactors when the WIP already has both.

## Related learnings

- [ship-tolerant-consumers-before-strict-producers.md](./ship-tolerant-consumers-before-strict-producers.md) — the general pattern this test-ordering technique instantiates.
- [integrate-review-findings-into-deepened-plan-without-split-brain.md](./integrate-review-findings-into-deepened-plan-without-split-brain.md) — the broader "constants require five synchronized updates" rule that the single-sourcing test is one expression of.

## Where this pattern applies

- Any monorepo invariant like "the production DB hostname appears only in `config/database.rb`".
- "This magic string is defined once" invariants.
- Feature-flag key single-sourcing tests.
- API route-path single-sourcing tests.
