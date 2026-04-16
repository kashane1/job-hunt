---
status: pending
priority: p1
issue_id: "013"
tags: [code-review, bug, batch-2]
dependencies: []
---

# Three runtime bugs in plan code blocks will fail at implementation

## Problem Statement

The deepening pass introduced three bugs in the code examples that would fail at runtime or test time. These are not architectural debates — they are copy-paste errors that need targeted fixes.

## Findings

### Bug 1: `_fetch_generic_html` keyword mismatch

- **Signature (plan line ~739):** `def _fetch_generic_html(url: str, html: str | None = None) -> dict:`
- **Caller (plan line ~796):** `fetched = _fetch_generic_html(url, html_text=html_override)`
- **Result:** `TypeError: _fetch_generic_html() got an unexpected keyword argument 'html_text'`
- **Found by:** architecture, python, security, pattern reviewers (agreed)

### Bug 2: `redirect_blocked` error code claimed but never emitted

- **Acceptance criterion:** "Redirect from allowed URL to private IP fails with `error_code: 'redirect_blocked'`"
- **Code:** `_StrictRedirectHandler.redirect_request` calls `_validate_url_for_fetch(newurl)` which raises `private_ip_blocked` (or `scheme_blocked`, etc.), NEVER `redirect_blocked`
- **Result:** Acceptance test will fail. Agents branching on `redirect_blocked` will never see it.
- **Found by:** architecture, python, agent-native reviewers

### Bug 3: `keyword_coverage` field missing from ats-check-report schema

- **Code emits:** both `keyword_coverage` and `keyword_density` in the `metrics` object
- **Schema declares:** only `keyword_density` in `metrics.properties`
- **Result:** Schema validation would silently drop `keyword_coverage`, or fail if strict
- **Found by:** architecture reviewer

## Proposed Solutions

### Option 1: Fix all three bugs in the plan (Recommended)

**For Bug 1:** Rename parameter to `html_text` in the signature:
```python
def _fetch_generic_html(url: str, html_text: str | None = None) -> dict:
```

**For Bug 2:** Wrap the redirect re-validation to emit the proper error_code:
```python
class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _validate_url_for_fetch(newurl)
        except IngestionError as exc:
            raise IngestionError(
                f"Redirect from {req.full_url} to {newurl} blocked: {exc}",
                error_code="redirect_blocked",
                url=newurl,
                remediation="The redirect chain led to a blocked destination.",
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)
```

**For Bug 3:** Add `keyword_coverage` to the schema:
```json
"keyword_coverage": { "type": "number" },
"keyword_density": { "type": "number" },
```

**Effort:** Small (plan edit only)
**Risk:** Low

## Recommended Action

Option 1. All three are pure fixes in the plan document. Must be done before implementation begins.

## Acceptance Criteria

- [ ] `_fetch_generic_html` signature and call sites agree on parameter name
- [ ] `redirect_blocked` error code is actually raised by `_StrictRedirectHandler`
- [ ] `ats-check-report.schema.json` includes `keyword_coverage` in `metrics.properties`
- [ ] Plan's `IngestionError` error_code enumeration includes `redirect_blocked`

## Work Log

### 2026-04-16 - Discovery

**By:** 4 parallel review agents (architecture, python, security, pattern)

**Actions:**
- Three independent agents flagged the `_fetch_generic_html` keyword bug
- Architecture and python agents flagged the `redirect_blocked` contract violation
- Architecture agent flagged the schema mismatch for `keyword_coverage`
