---
status: complete
priority: p1
issue_id: "027"
tags: [code-review, security, redaction, batch-2]
dependencies: []
re_ranked: "2026-05-18 p3->p1 — raw URL (userinfo/token) persisted on disk in a privacy-first repo"
---

# _intake/failed/ leaks fetched content and full URL; no redaction applied

## Problem Statement

Security reviewer noted: on failure, `ingest_url` moves the pending intake markdown to `failed/<ts>-<hash>.md` plus a `.err` file containing `URL: {url}\ncanonical: {canonical}\nerror: {exc}\n`. The intake markdown contains the raw fetched HTML. The `.err` file contains the full URL including any query params.

If the URL contains credentials (`https://user:pass@host/`) or session tokens in query strings, these persist on disk indefinitely. This violates the secret-handling pattern in batch 1's `design-secret-handling-as-a-runtime-boundary.md` solution.

## Findings

### Gaps

1. **URL userinfo not stripped** before writing to `.err`
2. **`redact_sensitive_data` from batch 1 not applied** to intake artifacts
3. **`failed/` directory unbounded** — old failures with stale URLs/content sit forever
4. **Error messages not redacted** — exception strings may contain credentials from 4xx responses

### Related existing pattern

Batch 1's `redact_sensitive_data` in `core.py` walks a dict and replaces values under keys matching `SENSITIVE_KEYWORDS`. This is designed for browser attempt payloads, but the pattern applies here: sanitize URL + body before persistence.

## Proposed Solutions

### Option 1: Redact at write boundary + retention policy (Recommended)

**URL sanitization helper:**
```python
def _sanitize_url_for_logging(url: str) -> str:
    """Remove userinfo and sensitive query params from a URL before logging."""
    parsed = urllib.parse.urlsplit(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    # Drop userinfo entirely
    safe_query = [
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
        if not any(sensitive in k.lower() for sensitive in ("token", "key", "secret", "password", "auth"))
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme, netloc, parsed.path,
        urllib.parse.urlencode(safe_query), "",
    ))
```

**Apply in .err writer:**
```python
safe_url = _sanitize_url_for_logging(url)
safe_canonical = _sanitize_url_for_logging(canonical)
safe_error = _sanitize_url_for_logging(str(exc))  # in case exception message embeds URL
failed_path.with_suffix(".err").write_text(
    f"URL: {safe_url}\ncanonical: {safe_canonical}\nerror: {safe_error}\n",
    encoding="utf-8",
)
```

**Retention policy:**
- `check-integrity` warns on `_intake/failed/` files older than 7 days
- `check-integrity --prune` deletes them

**Effort:** Small
**Risk:** Low

## Recommended Action

Option 1. Small, low-risk, closes a pattern-violation gap. Aligns batch 2 with batch 1's secret-handling solution.

## Acceptance Criteria

- [x] `_sanitize_url_for_logging` helper in `ingestion.py` (pre-existing)
- [x] Applied to URL, canonical, and exception string in `.err` writer (pre-existing)
- [x] Applied in any other log/error-write paths — the failed `.md` is now
      redacted via `_redact_failed_intake` instead of moved verbatim
- [x] `check-integrity` flags stale `failed/` entries (>7 days) — pre-existing
      `stale_intake_failed` (7-day threshold)
- [x] Test: `https://user:pass@host/...` → failed `.md` AND `.err` have no `user:pass`
- [x] Test: `?token=SECRET` → failed `.md` AND `.err` have no `SECRET`

## Work Log

### 2026-04-16 - Discovery

**By:** security-sentinel

**Actions:**
- Flagged `_intake/failed/` as leaking URL credentials and tokens
- Batch 1's `design-secret-handling` solution establishes the redact-at-boundary pattern to follow

### 2026-05-18 - Resolved (audit P1)

**By:** audit follow-up session

**Findings:** The `.err` URL/canonical/error sanitization and the
`stale_intake_failed` (>7d) check-integrity warning had already shipped
since this todo was filed. The one remaining leak was the failed `.md`
itself: `ingest_url` moved the pending intake **verbatim**, so its
`application_url` frontmatter still held the raw URL (userinfo / token
query params), and a reflected token could survive in the fetched body.

**Actions:**
- Added `_redact_failed_intake(text, url, canonical)` in `ingestion.py`:
  exact-substring replace of raw `url`/`canonical` (longest-first) with
  their `_sanitize_url_for_logging` form, then a `scheme://user:pass@`
  userinfo strip (`_USERINFO_RE`) as defense in depth.
- `ingest_url` now writes the redacted text to `failed/` and unlinks the
  pending file, instead of `intake_path.replace(failed_path)`.
- Added `FailedIntakeRedactionTest` (2 tests) proving neither the `.md`
  nor the `.err` retains `user:pass` or a `token=` secret, while the
  sanitized host is retained for triage.
- Re-ranked p3→p1: a raw credentialed URL persisting on disk in a
  privacy-first repo is a privacy leak, not a nit.
