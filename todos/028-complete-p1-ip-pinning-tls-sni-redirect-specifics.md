---
status: pending
priority: p1
issue_id: "028"
tags: [code-review, security, batch-3, discovery, ssrf]
dependencies: []
---

# Spell out TLS/SNI/cert validation and per-redirect pin for IP-pinned HTTPConnection

## Problem Statement

Batch 3 plan claims to close the DNS-rebinding TOCTOU inherited from batch 2 via a Phase 1 patch to `ingestion._fetch`: "custom HTTPConnection subclass that pins the validated IP and sets Host: header." The approach is correct in outline but the plan never shows the HTTPS branch, redirect-refresh semantics, or cert-validation posture. If the implementer gets any of these wrong, the "fix" silently makes TLS worthless or fails to pin at all.

Without this spelled out, the security claim at the top of the plan is load-bearing but unverifiable.

## Findings

Evidence from security review:

- **TLS/SNI/cert validation is unspecified.** Pinning to a pre-resolved IP via `HTTPSConnection` requires `socket.create_connection((ip, 443))` then `ssl_context.wrap_socket(sock, server_hostname=hostname)`. If implementer passes `server_hostname=ip`, SNI breaks and cert CN/SAN validation fails against the hostname. If they set `check_hostname=False` or `CERT_NONE` to "make it work," TLS becomes worthless.
- **Residual race across redirects.** `_StrictRedirectHandler` re-validates URLs but unless each new `HTTPSConnection` in the redirect chain is *also* IP-pinned with a fresh SSRF validation, a redirect to an attacker-controlled same-host CNAME still re-resolves via the OS.
- **Connection pool reuse.** If `http.client` keeps a pool keyed on `(host, port)`, a reused connection may bypass the pinned `connect()`.

Test `test_fetch_dns_rebinding_pinned_ip` as named proves connection-IP pinning, not that cert validation still works against a real HTTPS endpoint.

Plan location: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Phase 1 Deliverables, line ~905.

## Proposed Solutions

### Option 1: Add a concrete code block showing the full pinned-HTTPSConnection

**Approach:** Add ~30-line code block to plan showing `_PinnedHTTPSConnection(http.client.HTTPSConnection)` with `connect()` overridden:
- `socket.create_connection((self._pinned_ip, self.port), timeout=...)`
- `self.sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)`
- Default `ssl.create_default_context()` with `check_hostname=True` and `CERT_REQUIRED`
- `Host:` header set unconditionally before send

Add explicit acceptance criteria:
- Cert validation still passes against a public HTTPS endpoint (integration-test against a known-safe URL with a fixture CA bundle).
- `ssl_context.check_hostname is True`.
- `ssl_context.verify_mode == ssl.CERT_REQUIRED`.

**Pros:** Eliminates all implementer guesswork. Test can be written from the spec.
**Cons:** Adds ~30 lines to an already long plan.
**Effort:** Medium (1-2 hours plan edit, small implementation cost).
**Risk:** Low.

### Option 2: Disable keep-alive + re-pin on every redirect hop

**Approach:** In addition to Option 1, add `Connection: close` header and explicitly re-resolve + re-validate + re-pin in `_StrictRedirectHandler.redirect_request`.

**Pros:** Closes pool-reuse and per-redirect races.
**Cons:** Performance hit for non-attacker redirect chains (small).
**Effort:** Small (inside Option 1).
**Risk:** Low.

### Option 3: Ship it and document limitations

**Approach:** Mark the TOCTOU defense as "best effort, stdlib limitations apply" and defer full closure.

**Pros:** Ships faster.
**Cons:** Plan's Enhancement Summary #7 would be untruthful — it claims the pin closes TOCTOU.
**Risk:** High (security claim without verification).

## Recommended Action

(Filled during triage.)

## Technical Details

**Affected files:**
- `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` §Phase 1 Deliverables
- `src/job_hunt/ingestion.py` — add `_PinnedHTTPSConnection` + adapt `_fetch`

**Related components:**
- `_StrictRedirectHandler` in ingestion.py
- `_validate_url_for_fetch` in ingestion.py

## Acceptance Criteria

- [ ] Plan specifies the HTTPS branch of IP-pinning including `server_hostname`, `check_hostname=True`, `verify_mode=CERT_REQUIRED`.
- [ ] Plan specifies per-redirect pin refresh via `_StrictRedirectHandler.redirect_request`.
- [ ] Plan specifies `Connection: close` OR proves pool safety explicitly.
- [ ] `test_fetch_dns_rebinding_pinned_ip` asserts both (a) connection IP and (b) cert validation against real hostname.
- [ ] New test: `test_fetch_https_pinned_ip_cert_validates` against a fixture/known-good HTTPS endpoint.
- [ ] New test: `test_fetch_redirect_re_pins_ip`.

## Work Log

### 2026-04-16 - Discovered during post-deepen security review

**By:** security-sentinel agent

**Findings:**
- IP-pinning pattern named correctly but not detailed enough to implement safely.
- Test as currently named proves connection pinning but not cert validation integrity.

## Resources

- Plan: `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md`
- Related: batch-2 SSRF hardening at `src/job_hunt/ingestion.py:187-241`
- Solution doc: `docs/solutions/workflow-issues/review-deepened-plans-before-implementation.md` (inspiration — IPv6 / DNS-rebinding caught at plan-edit cost)
