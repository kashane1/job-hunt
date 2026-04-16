---
status: pending
priority: p1
issue_id: "014"
tags: [code-review, security, ssrf, batch-2]
dependencies: []
---

# SSRF hardening has three residual gaps: DNS rebinding, IPv6, decompression bombs

## Problem Statement

The deepening pass added SSRF guards (scheme allowlist, private-IP blocking, redirect re-validation), but the security reviewer identified three gaps where the claimed protection doesn't hold at runtime.

## Findings

### Gap 1: DNS rebinding is not actually mitigated (P1)

The plan's `_validate_url_for_fetch` resolves the hostname once to validate the IP. Then `_fetch` passes the **original hostname** (not the validated IP) to `urllib.request.Request`. `urllib` independently re-resolves via the OS resolver. A malicious DNS server can return a public IP on the first lookup (validator pass) and `169.254.169.254` / `127.0.0.1` on the second (actual fetch). The comment in the code says "caller responsibility" but the caller IS `_fetch` and it doesn't pin the IP.

### Gap 2: IPv6 private ranges not handled (P2 elevated to P1 by impact)

`socket.gethostbyname` is IPv4-only. For IPv6-only hosts:
- Returns `gaierror` → ingestion fails on all public IPv6-only hosts (correctness)
- If the host has both A and AAAA, `gethostbyname` gets the A, but urllib's `getaddrinfo` may return AAAA and connect to a private IPv6 address like `fe80::`, `fc00::/7` (ULA), or `::1` (security)

### Gap 3: Gzip/deflate decompression bomb (P1)

`gzip.decompress(raw)` on a 2MB gzip input can expand to gigabytes. Consumes memory, potentially kills the process. No size cap on the decompressed output.

## Proposed Solutions

### Option 1: Full fix — pin IP, use getaddrinfo, stream decompression (Recommended)

**DNS rebinding fix:** Resolve once, pin the IP, set `Host:` header manually:
```python
def _fetch(url: str, timeout: int = 10, max_bytes: int = 2_000_000) -> str:
    host, ip = _validate_url_for_fetch(url)  # returns (hostname, resolved IP)
    parsed = urllib.parse.urlsplit(url)
    # Build URL with the IP as host but pass original host via Header
    pinned_url = parsed._replace(netloc=_swap_host(parsed.netloc, ip)).geturl()
    req = urllib.request.Request(
        pinned_url,
        headers={"Host": host, ...},
    )
    # urllib honors the Host header; TLS SNI uses the URL host (the IP)
    # which is wrong for HTTPS — need a custom HTTPSConnection that sets
    # server_hostname=host while connecting to ip.
```

Alternative (simpler): subclass `HTTPSConnection` with an overridden `_get_hostport` that returns the pre-validated IP; set `server_hostname=host` for SNI.

**IPv6 fix:** Replace `socket.gethostbyname` with `socket.getaddrinfo`, validate ALL returned addresses:
```python
def _validate_url_for_fetch(url: str):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise IngestionError(..., error_code="scheme_blocked")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise IngestionError(..., error_code="dns_failed")
    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise IngestionError(..., error_code="private_ip_blocked")
    return parsed.hostname, infos
```

**Decompression bomb fix:** Stream + cap:
```python
MAX_DECOMPRESSED_BYTES = 5_000_000  # 5MB decompressed cap

if encoding in ("gzip", "deflate"):
    decoder = gzip.GzipFile(fileobj=io.BytesIO(raw)) if encoding == "gzip" else _DeflateStream(raw)
    chunks = []
    total = 0
    while True:
        chunk = decoder.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_DECOMPRESSED_BYTES:
            raise IngestionError(
                f"Decompressed size exceeded {MAX_DECOMPRESSED_BYTES}",
                error_code="decompression_bomb",
            )
        chunks.append(chunk)
    raw = b"".join(chunks)
```

**Effort:** Medium (IP pinning is the trickiest part; getaddrinfo + decompression cap are small)
**Risk:** Medium — the IP-pinning HTTPSConnection subclass is fiddly; add focused tests

### Option 2: Weaker mitigation — accept the risk

Single-user CLI on the user's own laptop. Attacker would need to craft a malicious URL AND get the user to paste it AND have the user's DNS resolver be attacker-controlled. Low real-world probability.

Accept and document the residual risk. Keep current guards (scheme + IPv4 private block + redirect re-validation).

**Effort:** None
**Risk:** Accepts known gaps; undermines the security hardening claims in the plan

## Recommended Action

Option 1. Security review caught these and they have known mitigations. Plan should be updated with the full fix in the `_validate_url_for_fetch` and `_fetch` code blocks, and the deliverables should list "IPv6 coverage via getaddrinfo" and "decompression bomb cap" and "DNS rebinding via IP pinning" as explicit line items.

## Acceptance Criteria

- [ ] `_validate_url_for_fetch` uses `socket.getaddrinfo` and validates all returned addresses
- [ ] IPv6 private/loopback/ULA/link-local ranges are rejected
- [ ] `_fetch` connects to the validated IP, not re-resolves via urllib
- [ ] Gzip/deflate decompression has a hard size cap (`decompression_bomb` error_code)
- [ ] Tests cover: AAAA to `::1`, AAAA to `fc00::1`, DNS rebinding via test server, 100:1 compression ratio bomb

## Work Log

### 2026-04-16 - Discovery

**By:** security-sentinel

**Actions:**
- Identified DNS rebinding TOCTOU gap
- Identified IPv6 resolution gap (gethostbyname is IPv4-only)
- Identified gzip decompression bomb vector
