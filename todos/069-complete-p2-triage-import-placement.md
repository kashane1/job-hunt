---
status: complete
priority: p2
issue_id: "069"
tags: [code-review, quality, triage]
dependencies: []
---

# triage.py mid-file + function-local imports violate repo import norm

## Problem Statement

`src/job_hunt/triage.py:~516-522` places `from datetime import ...` and
`from .confirmation import _dkim_pass, _quarantine, match_message`
mid-file with `# noqa: E402`, and `_bridge_to_stage` has a function-local
`from .utils import write_json`. There is **no import cycle**
(`confirmation`/`utils` do not import `triage`), so the deferral is
unjustified — every reference module keeps imports in one top block, and
the repo only uses deferred imports as function-local cycle-breakers with
an explanatory comment.

## Recommended Action

Hoist all of these into the top import block; delete the `# noqa: E402`
and the function-local `write_json` import. Pure refactor, no behavior
change.

## Acceptance Criteria

- [ ] No mid-file / function-local imports in `triage.py` (no real cycle exists).
- [ ] Full suite green.

## Work Log

- 2026-05-18: Found by kieran-python-reviewer, architecture-strategist, pattern-recognition-specialist (PR #4).

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
