---
title: Pin validated IP at the socket layer to close DNS-rebinding TOCTOU in SSRF-safe fetch
date: 2026-04-16
module: ingestion
problem_type: security_issue
component: src/job_hunt/ingestion.py
symptoms:
  - SSRF validator resolved a hostname to a public IP, but urllib's later fetch re-resolved DNS and could connect to a private or link-local address (169.254.169.254, 10.0.0.0/8, 127.0.0.0/8)
  - A short-TTL attacker-controlled DNS record could flip between a public IP at validation time and an internal IP at connect time, bypassing _ip_is_disallowed checks entirely
  - IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1, ::ffff:169.254.169.254) slipped past the SSRF allowlist because the disallow check did not unwrap the v4 mapping before comparing against private ranges
  - Both ingest-url (batch 2) and the new active-discovery fetcher (batch 3) shared the same urllib opener, so the bypass applied to every outbound HTTP(S) call in the ingestion surface
  - Redirects were honored by the default opener, meaning a validated public URL could 30x into an internal IP without re-running SSRF validation on the redirect target
root_cause: The SSRF guard performed hostname resolution and IP-range checks in _validate_url_for_fetch, but then handed the original URL (hostname string) to urllib.request.urlopen, which resolves DNS again inside the socket layer. Between those two resolutions, DNS can legitimately change (short TTL, round-robin, rebinding attack), so the IP that was validated was not necessarily the IP that got connected to — a classic time-of-check/time-of-use gap. Compounding this, _ip_is_disallowed compared ipaddress objects directly against IPv4 private ranges without normalizing IPv4-mapped IPv6 addresses, so ::ffff:-prefixed forms of private/link-local IPs were treated as public. The fix introduces _PinnedHTTPSConnection / _PinnedHTTPConnection that accept a pre-validated IP and connect to it directly while preserving the original hostname for SNI and TLS certificate verification (verify_mode=CERT_REQUIRED), wired through a custom opener (_build_pinned_opener) and a _StrictRedirectHandler that re-validates every redirect target — eliminating the second DNS lookup entirely.
tags:
  - security
  - ssrf
  - dns-rebinding
  - toctou
  - tls
  - ingestion
  - url-fetching
  - redirects
severity: critical
---

# Pin validated IP at the socket layer to close DNS-rebinding TOCTOU in SSRF-safe fetch

## Summary

Anyone building an SSRF-safe URL fetcher in Python's stdlib cares about this: the obvious shape — validate the URL, then call `urlopen` — is exploitable because DNS is resolved twice and an attacker controls the gap. You'll hit this the first time you accept a user-supplied URL (job-posting imports, webhook targets, link previews, OG-tag scrapers) and try to block the cloud metadata endpoint or internal ranges with a pre-flight IP check.

## Problem

### What did not work

Batch 2's approach was resolve-then-validate: `_validate_url_for_fetch` called `socket.getaddrinfo(hostname)`, walked the returned IPs, rejected any private/loopback/reserved address, and then returned control to `urllib.request.urlopen(url)`. The problem is that `urlopen` is given the original *hostname*, not the IP we just validated — so the stdlib HTTP stack performs its own independent DNS lookup when it opens the socket. A hostile authoritative DNS server can answer the first query with a public IP (passing validation) and the second with `127.0.0.1` or `169.254.169.254` (cloud metadata), landing the connect on a forbidden target. The flow looked roughly like:

```python
# BEFORE (batch 2 — vulnerable)
hostname = urlsplit(url).hostname
for info in socket.getaddrinfo(hostname, None):      # lookup #1
    if _ip_is_disallowed(ipaddress.ip_address(info[4][0])):
        raise IngestionError(..., "private_ip_blocked")
with urllib.request.urlopen(url, timeout=timeout) as resp:  # lookup #2 — independent!
    body = resp.read(max_bytes + 1)
```

The two lookups are independent; the attacker controls the gap.

### Root cause

Passing the hostname to `urlopen` hands DNS resolution back to the OS, which defeats the validation entirely: the guard only inspects the IPs returned by the *first* resolution, but the socket eventually connects to whatever the *second* resolution returns. Validation and connect must share a single resolved IP, or the check is decorative.

Compounding this, `_ip_is_disallowed` did not unwrap IPv4-mapped IPv6 addresses before range-checking, so `::ffff:127.0.0.1` / `::ffff:10.0.0.1` / `::ffff:169.254.169.254` all bypassed the allowlist on Python versions where `IPv6Address.is_private` returns `False` for mapped forms.

## Working solution

The fix makes the connect use a specific IP we already validated, while keeping every TLS invariant tied to the hostname.

1. `_PinnedHTTPSConnection` subclasses `http.client.HTTPSConnection` and overrides `connect()` so the TCP socket goes to the pinned IP directly (no further DNS), but `wrap_socket` still receives `server_hostname=self.host` — so SNI, `check_hostname=True`, and `verify_mode=CERT_REQUIRED` all validate against the hostname's cert CN/SAN. The `Host:` header is also the hostname (http.client default).
2. `_PinnedHTTPHandler` / `_PinnedHTTPSHandler` wire the pinned connection factory into a urllib opener by implementing `http_open` / `https_open` over `do_open(self._build_conn, req)`.
3. `fetch()` calls `_validate_url_for_fetch(url)` to get the validated IP list, picks `ip_strs[0]` as the pin, builds a *fresh* opener for this single request via `_build_pinned_opener`, and sets `Connection: close` on the request so no pooled (host, port) socket can be reused across calls to bypass the pin.
4. `_StrictRedirectHandler.redirect_request` calls `_validate_url_for_fetch(newurl)` on every hop and re-wraps any failure as `error_code="redirect_blocked"`, capped at 3 redirects — so a 302 to a metadata IP is rejected before any connect.
5. `_ip_is_disallowed` separately closes the IPv4-mapped IPv6 bypass: some Python versions report `::ffff:127.0.0.1` as `is_private=False`, so we explicitly re-check the embedded IPv4.

The pinned-connect:

```python
class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip, port=443, context=None, timeout=None):
        if context is None:
            context = ssl.create_default_context()
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip, self.port),
            timeout=self.timeout,
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
```

The IPv4-mapped-IPv6 guard:

```python
def _ip_is_disallowed(ip: ipaddress._BaseAddress) -> bool:
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    ):
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        mapped = ip.ipv4_mapped
        if (
            mapped.is_private or mapped.is_loopback or mapped.is_link_local
            or mapped.is_reserved or mapped.is_multicast or mapped.is_unspecified
        ):
            return True
    return False
```

TLS invariants held: `server_hostname=self.host`, `check_hostname=True`, `verify_mode=ssl.CERT_REQUIRED`, `Connection: close` on every request to prevent pool reuse bypassing the pin.

## Verification

The IPv4-mapped-IPv6 branch is exercised by `Ipv4MappedIpv6Test` in [tests/test_foundation.py](../../../tests/test_foundation.py):

- `test_mapped_loopback_disallowed` — `::ffff:127.0.0.1` is rejected
- `test_mapped_private_disallowed` — `::ffff:10.0.0.1` is rejected
- `test_mapped_public_allowed` — `::ffff:8.8.8.8` passes

The full 156-test batch-2 suite continues to pass, which is the regression guarantee that pinning + redirect revalidation didn't break any existing fetch, canonicalization, intake-lifecycle, or extract-lead behavior.

## Prevention strategies

1. **Validate-then-use pattern.** Any value you validate must be the exact same bytes you consume — never revalidate a name and then re-resolve it downstream. Analogues: (a) `os.path.realpath()` checked, then the file opened by its original symlinked path (classic TOCTOU — open the fd you stat'd, not the name); (b) decoding a JWT, asserting `sub`, then re-fetching the cookie/header from the request on a later middleware hop (use the claims captured at verify time); (c) any SSRF guard that resolves a hostname, inspects the IP, then hands the hostname — not the IP — to the HTTP client. The invariant: **validated artifact ≡ consumed artifact**, enforced by passing the resolved value through, not the name.

2. **TLS integrity when pinning IPs.** Three invariants must hold simultaneously:
   - **SNI = original hostname.** The `server_hostname` on the SSL wrap must be the user-facing hostname, not the pinned IP literal. Drop this and the server returns its default vhost cert — validation against the intended hostname silently fails or, worse, passes against an unrelated cert.
   - **Certificate hostname validation stays on** (`check_hostname=True`, `CERT_REQUIRED`). Pinning the IP without hostname verification reduces TLS to "some cert from some CA for some host" — an attacker with any valid cert on that IP wins. IP pinning only defends the transport; cert validation still has to defend identity.
   - **No connection reuse / pool sharing across resolutions.** A pooled keep-alive connection created under one resolution must not be reused for a later request that re-validated against a different IP; conversely, the pinned socket must not be returned to a shared pool keyed by hostname. Mix these and a stale connection bypasses the new check.

   Dropping any one makes the other two worthless: no SNI → wrong cert presented; no hostname check → pin accepts any valid cert on that IP; pooled reuse → TOCTOU moves from DNS to the pool.

3. **IPv4-mapped IPv6 gotcha.** `ipaddress.ip_address("::ffff:10.0.0.1").is_private` has returned `False` on older CPython (fixed in 3.11.4 / 3.12) and is still a footgun in mixed-version fleets. `getaddrinfo` with `AF_UNSPEC` will happily hand you v4-mapped v6 addresses on dual-stack boxes. Always normalize before the privacy check:

   ```python
   ip = ipaddress.ip_address(addr)
   if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
       ip = ip.ipv4_mapped
   if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
       reject()
   ```

   Same unwrap for `::` (unspecified) and `::1` (loopback-as-v6) — check the v4-mapped form and the v6 form.

4. **Redirect re-validation.** Every 3xx hop is a fresh DNS lookup and a fresh SSRF surface. A 302 from `public.example` → `internal.corp` (or → `http://[::ffff:169.254.169.254]`) hits the same TOCTOU if you only validated the first hostname. Either (a) disable auto-redirects and re-enter the validate-resolve-pin pipeline per hop, or (b) install a redirect handler that runs the full guard — IP classification, v4-mapped unwrap, pin — on every `Location:` before following. Cap redirect depth (≤5) so a redirect loop can't exhaust the guard.

5. **Test the pin.** Two tests earn their keep:
   - **DNS-flip test.** Monkey-patch `socket.getaddrinfo` (or inject a resolver) to return `203.0.113.10` on call 1 and `127.0.0.1` on call 2. Run the full fetch. Assert the TCP `connect()` target equals the call-1 IP (inspect via a wrapped socket factory or a mock `socket.socket.connect`). Regression bait: any future refactor that re-resolves inside `urlopen` fails this.
   - **Fixture-CA test.** Stand up an HTTPS server on 127.0.0.1 with a cert for `pinned.test`, add the fixture CA to the trust store, map `pinned.test` → 127.0.0.1 via the pin, and assert a successful fetch. Then swap the cert for one issued to `other.test` and assert `SSLCertVerificationError`. Proves SNI + hostname validation survived the pinning.

## Test cases to add

- `test_dns_rebind_second_resolution_ignored` — getaddrinfo returns safe IP then internal IP; assert socket connects to the first IP and request succeeds against it.
- `test_ipv4_mapped_ipv6_private_rejected` — resolver returns `::ffff:10.0.0.1`; assert the fetch is rejected with the private-IP error, not accepted.
- `test_redirect_to_internal_host_rejected` — public host 302s to `http://169.254.169.254/latest/meta-data/`; assert the redirect is refused before any connection to the metadata IP.
- `test_pinned_connection_validates_hostname_cert` — fixture CA + cert for `pinned.test` pinned to 127.0.0.1 succeeds; same pin with a cert for `wrong.test` raises `SSLCertVerificationError`.
- `test_pinned_socket_not_returned_to_pool` — two sequential fetches to the same hostname with different resolved IPs each open a fresh socket to the freshly validated IP (no keep-alive reuse of the first pin).
- `test_sni_matches_hostname_not_ip` — intercept the TLS ClientHello on a pinned connection and assert `server_name` equals the original hostname, not the pinned IP literal.

## Related documentation

- [docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md](../../plans/2026-04-16-004-feat-active-job-discovery-plan.md) — batch 3 plan that specified the Phase 1 P0 security prerequisite (todos #013/#014/#028) requiring `_PinnedHTTPSConnection`, re-pin-on-redirect `_StrictRedirectHandler`, and the explicit IPv4-mapped-IPv6 check in `_validate_url_for_fetch`.
- [docs/plans/2026-04-16-003-feat-pdf-url-ats-analytics-plan.md](../../plans/2026-04-16-003-feat-pdf-url-ats-analytics-plan.md) — batch 2 where the URL-ingestion layer (scheme allowlist, private-IP blocking, decompression caps, structured error codes) shipped and where the TOCTOU between `_validate_url_for_fetch` and `_fetch` was latent before this fix closed it.
- [docs/guides/job-discovery.md](../../guides/job-discovery.md) — user-facing guide for `discover-jobs` that documents how polling flows through `ingestion.fetch` and inherits this SSRF posture.
- [docs/solutions/security-issues/design-secret-handling-as-a-runtime-boundary.md](design-secret-handling-as-a-runtime-boundary.md) — prior security-issues solution establishing the "treat sensitive runtime concerns as an explicit boundary" pattern; this fix extends the same pattern to network trust (DNS/IP as a boundary, not an assumption).
- [AGENTS.md](../../../AGENTS.md) — "URL ingestion safety" and "Batch 3 — Discovery Guardrails" sections document the agent-facing invariant: every HTTP call goes through `ingestion.fetch`, which pins the validated IP while preserving TLS hostname validation.

## Related pull requests

- [kashane1/job-hunt#1](https://github.com/kashane1/job-hunt/pull/1) — landing PR for the DNS-rebinding TOCTOU and IPv4-mapped-IPv6 SSRF bypass fix via `_PinnedHTTPSConnection` in `src/job_hunt/ingestion.py`.
