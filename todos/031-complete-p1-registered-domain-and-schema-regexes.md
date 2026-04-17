---
status: pending
priority: p1
issue_id: "031"
tags: [code-review, security, batch-3, discovery, schemas]
dependencies: []
---

# `registered_domain()` edge cases, watchlist `name` regex, and entry_id regex

## Problem Statement

Three related issues converge on input-sanitization and domain-parsing correctness:

1. `registered_domain(url)` returns `".".join(parts[-2:])` for unknown hosts. For IP URLs (`http://1.2.3.4/x`), this returns `"3.4"` — wrong. For empty hostnames, returns `""` and all hostless URLs share one budget. No IDN/Punycode normalization.
2. Watchlist `name` field has no character-class restriction. `name: "../../../etc/passwd"` becomes a path segment if `name` ever appears in a filename (review filenames, cursor keys) — file-write primitive.
3. `entry_id` for review files has no documented format. Without a regex constraint, an attacker with write access to `data/discovery/review/` could drop `../../evil.json` and have `review-promote` resolve paths dangerously.

## Findings

- **`registered_domain` edge cases** (Kieran Python review): IP URLs bucketed incorrectly; empty hostnames share budget; IDN/Punycode unhandled. For `http://xn--...`-style or Unicode hostnames, inconsistent bucketing means different representations of the same host get different budgets.
- **`name` path-traversal** (Security review P1 #9): watchlist schema requires `name` exists but shows no pattern. If entry_id is derived from name OR name appears in a path anywhere, `name: "../../../../etc/passwd"` is a write-primitive. Plan lists `data/discovery/review/<entry_id>.{md,json}` — derivation of entry_id not explicit.
- **SOURCE_NAME_MAP colon collision** (Security review P3 #10): cursor keys `company:source` would corrupt parsing if a company name contained a colon. Should use a separator forbidden by the `name` regex.

## Proposed Solutions

### Option 1: Single todo, three concrete fixes

**Fix A — `registered_domain`:**
```python
import ipaddress
def registered_domain(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"No hostname in {url!r}")
    # Bucket IPs whole
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    # IDN normalize
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    for known in KNOWN_SHARED_DOMAINS:
        if host == known or host.endswith(f".{known}"):
            return known
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host
```

**Fix B — watchlist schema `name` pattern:**
Add to `schemas/watchlist.schema.json`:
```json
"name": { "type": "string", "pattern": "^[A-Za-z0-9 ._-]{1,64}$" }
```

**Fix C — entry_id format:**
Specify in plan: `entry_id = short_hash(canonical_url + discovered_at)` (16 hex chars); schema pattern `^[a-f0-9]{16}$` in `discovery-review.schema.json`. `review-promote` validates entry_id against this regex before any filesystem access.

**Fix D — cursor key separator:**
Change from `company:source` to `company|source` (pipe forbidden by name regex above), OR JSON-encode the tuple as the key.

**Effort:** Small-medium. Risk: Low.

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Module structure `registered_domain()`, §Schema additions (watchlist.schema.json, discovery-review.schema.json), §cursor key format
- `src/job_hunt/utils.py` — `registered_domain`
- `schemas/watchlist.schema.json` — `name` pattern
- `schemas/discovery-review.schema.json` — `entry_id` pattern
- `schemas/discovery-cursor.schema.json` — `entries` key separator

## Acceptance Criteria

- [ ] `registered_domain` handles IP URLs, empty hostnames (raise), IDN Punycode.
- [ ] `watchlist.schema.json` enforces `name` pattern rejecting path-traversal.
- [ ] `discovery-review.schema.json` enforces `entry_id` regex.
- [ ] `review-promote` validates entry_id against regex before filesystem I/O.
- [ ] Cursor key separator is not a valid character in `name` regex.
- [ ] Tests: `test_registered_domain_ip_url`, `test_registered_domain_empty_hostname`, `test_registered_domain_idn`, `test_watchlist_name_rejects_path_traversal`, `test_review_promote_rejects_entry_id_traversal`.

## Work Log

### 2026-04-16 - Discovered during post-deepen review

**By:** kieran-python-reviewer, security-sentinel

**Findings:** Three independent issues converge on input-sanitization — fixable as a single plan patch.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
