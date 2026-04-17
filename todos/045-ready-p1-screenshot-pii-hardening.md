---
status: ready
priority: p1
issue_id: 045
tags: [code-review, security, privacy, indeed-auto-apply]
dependencies: []
decided_at: 2026-04-17
decision: option-1-pillow
---

# Screenshot PII hardening — DECIDED: Option 1 (Pillow + sanitizer module)

## Decision (2026-04-17)

User approved **Option 1**: add Pillow as an explicit dependency and build `src/job_hunt/screenshot_sanitizer.py` with a `sanitize(image_bytes, regions)` function. This accepts the stdlib-only deviation; screenshot PII hardening is considered worth the first non-stdlib dependency.

**Implementation contract** (for Phase 5 / Phase 9):
- `pyproject.toml` declares `Pillow>=10.0` as an install-time dependency.
- `src/job_hunt/screenshot_sanitizer.py` exposes `sanitize(image_bytes: bytes, regions: list[BoundingBox]) -> bytes`.
- Gaussian blur with radius ~25px on each region.
- Agent produces the region list by running address/phone/email regex over `mcp__Claude_in_Chrome__get_page_text` output and capturing bounding boxes via `read_page` / `find` selectors.
- Before write to `data/applications/{draft_id}/checkpoints/*.png`, every screenshot passes through `sanitize`.
- Sanitizer attaches a `sanitized_at` PNG text-chunk metadata tag; `check-integrity` warns on any checkpoint PNG missing this tag.
- Unit test: fixture image with known PII regions → blurred output verified via pixel-comparison on the blurred region.

# Screenshot PII hardening needs a concrete PIL implementation, not just prose (original proposal below)

## Problem Statement

The plan at `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md` states that `checkpoints/*.png` screenshots must be (a) cropped to form area only (excluding browser chrome, tabs, extensions), and (b) post-captured through a PIL pass blurring fields matching address/phone/email regex. Gitignore protects against commits, but the files sit on disk with PII.

Currently the plan specifies the intent (Enhancement Summary item 12, NFR section) but no implementation. No Python helper is named, no PIL dependency is declared (currently stdlib-only), and no playbook step references the crop+blur invocation.

## Findings

- NFR: "Screenshots at `data/applications/**/checkpoints/*.png`: cropped to form area only; post-capture PIL pass blurs fields matching address/phone/email regex."
- Phase 5 playbook skeleton Step 5/8: "Screenshot the form area ONLY (exclude browser chrome...). Run the post-capture PIL blur pass."
- No Python module owns the blur pass.
- Stdlib-only commitment is stated; `Pillow` (PIL) would be a new dep.

## Proposed Solutions

### Option 1: New `src/job_hunt/screenshot_sanitizer.py` module + Pillow dep (Recommended)
Add Pillow as an explicit dep in pyproject.toml. Module exposes `sanitize(image_bytes, text_hits: list[BoundingBox]) -> bytes` that takes the MCP screenshot output + bounding boxes of sensitive text (from `get_page_text` — the agent runs regex, gets field bounding boxes, passes to the sanitizer). The sanitizer blurs those regions with a Gaussian filter (radius 25px or so).

- Pros: Clean separation; testable on fixture images.
- Cons: First non-stdlib dep in the repo. Pillow is large (~50MB wheels) but universally available.
- Effort: Medium (1 day).
- Risk: Low.

### Option 2: MCP-side cropping, no blur
Rely on the agent to crop tightly enough that no PII is captured. Skip PIL entirely.

- Pros: No dependency.
- Cons: Crop-to-form doesn't eliminate PII; the form itself contains the PII (that's what the user is filling). Not a real mitigation.
- Risk: High — misses the real threat.

### Option 3: ImageMagick via subprocess (stdlib-only posture)
Shell out to `magick convert -blur 0x8 <region>`. No Python dep but requires ImageMagick on PATH.

- Pros: Preserves stdlib-only posture.
- Cons: Non-Python dep; subprocess launching adds latency; harder to test.
- Effort: Medium.
- Risk: Medium (availability varies across user systems).

## Recommended Action

Option 1. Pillow is a reasonable first non-stdlib dep given the security value; declare it explicitly. Alternatively, reconsider whether screenshots are actually needed for the audit trail — if the `plan.json` + `attempts/*.json` records the field values (redacted), a screenshot adds limited incremental value.

## Technical Details

- New file: `src/job_hunt/screenshot_sanitizer.py`
- New dep: `Pillow>=10.0` in pyproject.toml
- Updated playbook: Phase 5 playbooks call `sanitize()` before writing checkpoint PNG
- Test: fixture image with known PII regions → verify output has those regions blurred

## Acceptance Criteria

- [ ] `src/job_hunt/screenshot_sanitizer.py` exists with `sanitize(image_bytes, regions) -> bytes`
- [ ] `pyproject.toml` declares `Pillow>=10.0`
- [ ] Per-surface playbooks (Phase 5) call the sanitizer before checkpoint write
- [ ] Unit test: fixture image → blurred output verified via pixel-comparison on the blurred region
- [ ] `check-integrity` surfaces any checkpoint PNG that doesn't carry a `sanitized_at` metadata tag

## Work Log

- 2026-04-17: Created from technical-review pass on indeed-auto-apply plan.

## Resources

- Plan: `docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md` (Enhancement Summary item 12; Phase 5)
- Pillow: https://pillow.readthedocs.io/
