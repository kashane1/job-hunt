---
status: pending
priority: p2
issue_id: "039"
tags: [code-review, security, batch-3]
dependencies: []
---

# Anti-bot detection: HTTP status + body (not body alone); review file nonce-fence

## Problem Statement

Two security review findings on Phase 3 career crawler + review-file defense:

1. **Anti-bot detection regex on body alone is exploitable both ways.** A careers page with the literal word "cloudflare" in its footer (e.g. "protected by Cloudflare" disclosure) gets marked `anti_bot_blocked` and skipped forever — denial-of-discovery. Inversely, an attacker with control of any response could inject fake `cf-ray` to force skip.
2. **Fixed backtick code-fence in review markdown is trivially escapable.** Attacker-controlled anchor text containing ` ``` ` breaks out of the fence and returns to injection surface, defeating the HTML-escape defense.

## Findings

Security review:
- Recommendation: "Match on HTTP status 403/503 AND (header `cf-ray` OR title regex), not body regex alone."
- Recommendation: "Fence with a unique-nonce fence (e.g., seven backticks + random tag) per batch-2's nonce-delimited pattern, *or* strip backticks from anchor text before rendering."

## Proposed Solutions

### Option 1: Tighten anti-bot + nonce-fence review content

**Anti-bot fix:**
```python
def _detect_anti_bot(response_status: int, headers: dict, body: str) -> bool:
    if response_status not in (403, 503):
        return False
    if "cf-ray" in {k.lower() for k in headers}:
        return True
    if re.search(r"<title>\s*Just a moment", body, re.I):
        return True
    return False
```

Call AFTER fetch returns (not during body scanning). Requires `ingestion.fetch` to expose response status and headers, not just body.

**Review fence fix (mirror batch 2's nonce pattern):**
```python
nonce = secrets.token_hex(6)
fence = "```untrusted_data_" + nonce
safe = anchor_text.replace(fence, fence.replace("`", "'"))  # defensive collision
# Emit:
# {fence}
# {safe}
# ```
```

**Pros:** Both defenses become robust.
**Cons:** Requires `fetch` to return more than just body text (tuple or dict).
**Effort:** Small-medium.
**Risk:** Low.

### Option 2: Drop anti-bot detection entirely; strip backticks from anchor text

**Approach:** Let `fetch` return a 403 naturally (falls through to `failed` bucket). Sanitize anchor text by stripping `\`` before writing reviews.

**Pros:** Less code.
**Cons:** Loses the "mark this host as unfit" behavior across sources in the same run.
**Effort:** Minimal.
**Risk:** Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §discover_company_careers, §Review-file writer
- `src/job_hunt/ingestion.py` (if fetch's return type expands)

## Acceptance Criteria

- [ ] Anti-bot detection matches HTTP status AND header/title pattern, not body-regex-alone.
- [ ] Review file fence is nonce-derived OR anchor text has backticks stripped.
- [ ] Test: `test_anti_bot_requires_status_and_pattern_not_body_alone`.
- [ ] Test: `test_review_file_fence_resists_backtick_injection`.

## Work Log

### 2026-04-16 - Security review

**By:** security-sentinel

**Findings:** Body-regex anti-bot is both DoS-able and bypassable; fixed backtick fence loses to ` ``` ` injection.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- Batch 2 nonce pattern: `src/job_hunt/ingestion.py::_wrap_fetched_content`
