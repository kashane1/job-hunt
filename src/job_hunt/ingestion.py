"""URL → structured lead ingestion.

Fetches job postings from:
- Greenhouse public JSON API (boards-api.greenhouse.io)
- Lever public JSON API (api.lever.co/v0/postings)
- Generic HTML fallback for other platforms (marked weak_inference)

Security hardening (per batch 2 plan Phase 2):
- Scheme allowlist (http/https only) — refuses file://, ftp://, etc.
- Private-IP blocking via socket.getaddrinfo — covers IPv4 AND IPv6 private,
  loopback, link-local, multicast, reserved, unspecified ranges.
- Strict redirect handler re-validates each hop (max 3), wraps errors as
  `redirect_blocked` for agent consumption.
- Bounded fetch: 10s timeout, 2MB response limit.
- Decompression bomb cap: 5MB decompressed limit for gzip/deflate.
- LinkedIn/Indeed URLs hard-fail with `login_wall` — we do not scrape login walls.
- Prompt-injection defense: fetched descriptions wrapped in nonce-delimited
  `<fetched_job_description_v{nonce}>...</fetched_job_description_v{nonce}>` tags.

Data integrity:
- URL canonicalization strips ~25 tracking params before fingerprinting —
  two URLs for the same posting produce the same lead_id.
- Intake file lifecycle: _intake/pending/ → _intake/processed/ (success) or
  _intake/failed/ with .err sidecar (failure). Post-success rename failures
  log a warning but do NOT fake a failure (lead JSON is already persisted).
- Sensitive URL parts (userinfo, token query params) are redacted in .err files.

Threading:
- ingest_urls_file parallelizes via ThreadPoolExecutor(max_workers=5), but
  deduplicates input by canonical URL BEFORE dispatch to avoid same-fingerprint
  races on shared lead files.
"""

from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import json
import logging
import re
import secrets
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .net_policy import DISCOVERY_USER_AGENT, parse_retry_after
from .utils import StructuredError, now_iso, short_hash, slugify, write_json

logger = logging.getLogger(__name__)

INGESTION_ERROR_CODES: Final = frozenset({
    "login_wall", "scheme_blocked", "private_ip_blocked", "redirect_blocked",
    "rate_limited", "timeout", "not_found", "response_too_large",
    "decompression_bomb", "dns_failed", "http_error", "network_error",
    "invalid_url", "unexpected",
})


class IngestionError(StructuredError):
    """Structured error for URL ingestion failures.

    Inherits the shared `StructuredError` base so the CLI can catch
    `IngestionError`, `PdfExportError`, and `DiscoveryError` uniformly.
    """

    ALLOWED_ERROR_CODES = INGESTION_ERROR_CODES


@dataclass(frozen=True)
class FetchResult:
    """Result from a `fetch()` call — status + headers + body.

    Status and headers let callers detect bot-challenge pages (HTTP 403/503
    with `cf-ray` or a Cloudflare title) and branch on HTTP metadata without
    having to re-issue the request.
    """

    status: int
    headers: dict[str, str]
    body: str


# Platforms with public JSON APIs we can use without scraping HTML.
GREENHOUSE_URL_RE = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/(?P<company>[^/]+)/jobs/(?P<job_id>\d+)"
)
LEVER_URL_RE = re.compile(
    r"https?://jobs\.lever\.co/(?P<company>[^/]+)/(?P<job_id>[a-f0-9-]+)"
)
ASHBY_URL_RE = re.compile(
    r"https?://jobs\.ashbyhq\.com/(?P<company>[^/]+)/(?P<job_id>[a-f0-9-]+)"
)

# Sites that require login and cannot be reliably scraped. We refuse politely
# rather than silently scraping a login page as the job description.
#
# Domains in `config/domain-allowlist.yaml` bypass this check ONLY for the
# hard-fail guard — they still go through SSRF / TLS / fetch-size / rate-limit
# guards. See AGENTS.md Safety Overrides semantics: runtime overrides can
# tighten but not loosen the allowlist.
HARD_FAIL_URL_PATTERNS: Final = (
    re.compile(r"https?://(?:www\.)?linkedin\.com/jobs/"),
    re.compile(r"https?://(?:www\.)?indeed\.com/viewjob"),
)


def _load_login_wall_allowlist() -> frozenset[str]:
    """Load ``config/domain-allowlist.yaml`` into a frozen set of domains.

    Returns an empty set when the config file is missing — preserving the
    v1 "everything hard-fails" posture. Parse failures propagate so the
    misconfiguration surfaces loudly instead of silently re-enabling blocks.
    """
    from .utils import load_yaml_file, repo_root

    path = repo_root() / "config" / "domain-allowlist.yaml"
    data = load_yaml_file(path, {})
    if not data:
        return frozenset()
    entries = data.get("allowed", []) or []
    domains: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("domain")
        if isinstance(raw, str) and raw.strip():
            domains.add(raw.strip().lower())
    return frozenset(domains)


_ALLOWED_LOGIN_WALLED: Final[frozenset[str]] = _load_login_wall_allowlist()


def _netloc_in_allowlist(netloc: str, allowlist: frozenset[str]) -> bool:
    """True when ``netloc`` equals or is a subdomain of any allowlisted domain."""
    host = netloc.lower().split(":", 1)[0]
    for allowed in allowlist:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def is_hard_fail_url(url: str) -> bool:
    """True when the URL matches a login-wall pattern AND is not allowlisted.

    Callers (``ingest_url``, ``discovery.discover_company_careers``) use this
    to decide whether to raise the ``login_wall`` / ``hard_fail_platform``
    structured error. Centralizing the check keeps the allowlist policy in
    one place instead of threading it through fetch plumbing.
    """
    for pattern in HARD_FAIL_URL_PATTERNS:
        if pattern.search(url):
            try:
                netloc = urllib.parse.urlsplit(url).netloc
            except ValueError:
                return True
            if _netloc_in_allowlist(netloc, _ALLOWED_LOGIN_WALLED):
                return False
            return True
    return False

# Tracking params to strip for idempotent fingerprinting. Expanded from batch 1
# per research: Greenhouse, Lever, LinkedIn, HubSpot, major ad platforms, social.
TRACKING_PARAM_EXACT: Final = frozenset({
    "gh_src", "gh_jid", "source", "ref", "referer", "referrer",
    "sid", "campaign", "medium", "content",
    "ref_src", "ref_url",
    "lever-source", "lever-origin",
    "trk", "trkCampaign", "refId",
    "mc_cid", "mc_eid",
    "fbclid", "gclid", "msclkid", "yclid",
    "igshid", "ttclid",
})
TRACKING_PARAM_PREFIX: Final = ("utm_", "hsa_", "_hs")

ALLOWED_SCHEMES: Final = frozenset({"http", "https"})

MAX_FETCH_TIMEOUT_S: Final = 10
MAX_FETCH_BYTES: Final = 2_000_000
MAX_DECOMPRESSED_BYTES: Final = 5_000_000


# =============================================================================
# URL canonicalization
# =============================================================================

def canonicalize_url(url: str) -> str:
    """Strip tracking params, lowercase netloc, drop fragment, sort query.

    Used for idempotent fingerprinting — two URLs for the same posting should
    produce the same canonical form. Preserves case-sensitive path (Linux
    servers treat paths as case-sensitive per RFC 3986).
    """
    parsed = urllib.parse.urlsplit(url)
    kept_query = sorted(
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
        if not any(k.startswith(p) for p in TRACKING_PARAM_PREFIX)
        and k not in TRACKING_PARAM_EXACT
        and not k.endswith("clid")
    )
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        urllib.parse.urlencode(kept_query),
        "",  # drop fragment
    ))


def _sanitize_url_for_logging(url: str) -> str:
    """Remove userinfo and sensitive query params before logging.

    Prevents credentials or tokens from persisting in .err files or logs.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    safe_query = [
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
        if not any(s in k.lower() for s in ("token", "key", "secret", "password", "auth"))
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme, netloc, parsed.path,
        urllib.parse.urlencode(safe_query), "",
    ))


# =============================================================================
# SSRF guards
# =============================================================================

def _ip_is_disallowed(ip: ipaddress._BaseAddress) -> bool:
    """Check if an IP lives in a range we refuse to fetch.

    IPv4-mapped IPv6 addresses (`::ffff:127.0.0.1`) get their embedded IPv4
    checked explicitly — some Python versions report `is_private=False` for
    the mapped form even though the underlying IPv4 is loopback/private.
    """
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


def _validate_url_for_fetch(url: str) -> tuple[str, list[str]]:
    """SSRF guard — returns (hostname, list of resolved IPs).

    Blocks:
    - non-http(s) schemes
    - private/loopback/link-local/reserved/multicast/unspecified IPs (IPv4+IPv6)
    - IPv4-mapped IPv6 addresses whose embedded IPv4 is disallowed
    - ANY returned address is validated; if ANY is in a disallowed range, reject.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise IngestionError(
            f"Scheme {parsed.scheme!r} is not allowed (only http/https)",
            error_code="scheme_blocked",
            url=url,
            remediation="Use an http:// or https:// URL",
        )
    if not parsed.hostname:
        raise IngestionError(
            f"URL has no hostname: {url}",
            error_code="invalid_url",
            url=url,
        )
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise IngestionError(
            f"Could not resolve host {parsed.hostname}: {exc}",
            error_code="dns_failed",
            url=url,
        ) from exc
    ip_strs: list[str] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _ip_is_disallowed(ip):
            raise IngestionError(
                f"Refusing to fetch private/loopback/reserved address: {ip}",
                error_code="private_ip_blocked",
                url=url,
                remediation="Fetch only public job board URLs",
            )
        ip_strs.append(ip_str)
    if not ip_strs:
        raise IngestionError(
            f"No valid IP addresses for {parsed.hostname}",
            error_code="dns_failed",
            url=url,
        )
    return parsed.hostname, ip_strs


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that CONNECTs to a pre-validated IP while preserving
    full TLS integrity (SNI, hostname verification, cert validation).

    Closes the DNS-rebinding TOCTOU between `_validate_url_for_fetch` (which
    resolves and validates an IP) and `urlopen` (which would otherwise
    re-resolve via the OS). The socket connects to `pinned_ip`; the TLS
    wrapper sets `server_hostname=self.host` so SNI and cert CN/SAN
    validation still target the hostname. The HTTP `Host:` header carries
    the hostname by default (http.client behavior).
    """

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


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain-HTTP counterpart to `_PinnedHTTPSConnection`.

    No TLS here — there is no DNS-rebind vs. certificate conflict to balance.
    Used only when the caller explicitly asked for `http://`.
    """

    def __init__(self, host, pinned_ip, port=80, timeout=None):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port),
            timeout=self.timeout,
        )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ip: str, timeout: float):
        super().__init__()
        self._pinned_ip = pinned_ip
        self._timeout = timeout

    def https_open(self, req):
        return self.do_open(self._build_conn, req)

    def _build_conn(self, host, timeout=None, **kwargs):
        return _PinnedHTTPSConnection(
            host,
            pinned_ip=self._pinned_ip,
            timeout=timeout if timeout is not None else self._timeout,
        )


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned_ip: str, timeout: float):
        super().__init__()
        self._pinned_ip = pinned_ip
        self._timeout = timeout

    def http_open(self, req):
        return self.do_open(self._build_conn, req)

    def _build_conn(self, host, timeout=None, **kwargs):
        return _PinnedHTTPConnection(
            host,
            pinned_ip=self._pinned_ip,
            timeout=timeout if timeout is not None else self._timeout,
        )


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate each redirect target for SSRF. Cap at 3 hops.

    Wraps inner validation errors with error_code='redirect_blocked' so agents
    can distinguish direct rejections from rejections-via-redirect. The caller
    (fetch) re-resolves and re-pins the IP for each redirect hop by replaying
    the request with a fresh opener — this handler just enforces the
    validation boundary so a bad redirect target never reaches the pinned
    connection.
    """

    max_redirections = 3

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,
        code: int,
        msg: str,
        headers,
        newurl: str,
    ):
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


def _build_pinned_opener(pinned_ip: str, timeout: float):
    return urllib.request.build_opener(
        _PinnedHTTPSHandler(pinned_ip, timeout),
        _PinnedHTTPHandler(pinned_ip, timeout),
        _StrictRedirectHandler(),
    )


# =============================================================================
# Bounded fetch with decompression-bomb cap
# =============================================================================

def _decompress_bounded(raw: bytes, encoding: str, limit: int, url: str) -> bytes:
    """Stream-decompress with a hard size cap. Guards against compression bombs
    where a 2MB gzip expands to gigabytes."""
    if encoding == "gzip":
        stream = gzip.GzipFile(fileobj=io.BytesIO(raw))
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise IngestionError(
                    f"Decompressed size exceeded {limit} bytes",
                    error_code="decompression_bomb",
                    url=url,
                )
            chunks.append(chunk)
        return b"".join(chunks)
    # deflate
    decoder = zlib.decompressobj()
    chunks2: list[bytes] = []
    total = 0
    offset = 0
    while offset < len(raw):
        part = decoder.decompress(raw[offset:offset + 65536])
        offset += 65536
        if not part:
            continue
        total += len(part)
        if total > limit:
            raise IngestionError(
                f"Decompressed size exceeded {limit} bytes",
                error_code="decompression_bomb",
                url=url,
            )
        chunks2.append(part)
    tail = decoder.flush()
    if tail:
        total += len(tail)
        if total > limit:
            raise IngestionError(
                f"Decompressed size exceeded {limit} bytes",
                error_code="decompression_bomb",
                url=url,
            )
        chunks2.append(tail)
    return b"".join(chunks2)


def fetch(
    url: str,
    timeout: int = MAX_FETCH_TIMEOUT_S,
    max_bytes: int = MAX_FETCH_BYTES,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> FetchResult:
    """Stdlib HTTP GET with SSRF guards, IP pinning, timeout, size limits, gzip.

    Closes DNS-rebinding TOCTOU: the validated IP is pinned for the socket
    connect while TLS/SNI/cert validation target the hostname. Uses
    `Connection: close` to defeat pool reuse where a cached (host,port)
    connection could bypass the pin.

    Returns `FetchResult(status, headers, body)` — callers can branch on
    HTTP status and headers without issuing a second request.
    """
    _hostname, ip_strs = _validate_url_for_fetch(url)
    pinned_ip = ip_strs[0]
    opener = _build_pinned_opener(pinned_ip, float(timeout))
    # Shared Chrome UA from net_policy — the previous "job-hunt-cli/0.2"
    # identifier was an obvious bot signal. Browser-shaped Accept headers
    # keep the request looking like an ordinary page load.
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DISCOVERY_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, identity",
            "Connection": "close",
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            headers = {k.lower(): v for k, v in resp.headers.items()}
            content_length = resp.headers.get("Content-Length")
            if content_length:
                try:
                    declared_len = int(content_length)
                except ValueError:
                    declared_len = None
                if declared_len is not None and declared_len > max_bytes:
                    raise IngestionError(
                        f"Response Content-Length {declared_len} exceeds {max_bytes}",
                        error_code="response_too_large",
                        url=url,
                    )
            raw = resp.read(max_bytes + 1)
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            code = "rate_limited"
            # Surface Retry-After per RFC 9110 §10.2.3 — accepts both
            # delta-seconds and HTTP-date. Callers receive the structured
            # error with the cool-down window parsed out for logging/backoff.
            raw_retry = ""
            try:
                raw_retry = exc.headers.get("Retry-After", "") if exc.headers else ""
            except Exception:
                raw_retry = ""
            retry_seconds = parse_retry_after(raw_retry) if raw_retry else None
            parts: list[str] = [f"HTTP {exc.code} from {url}"]
            if retry_seconds is not None:
                parts.append(f"(Retry-After: {int(retry_seconds)}s)")
            elif raw_retry:
                parts.append(f"(Retry-After: {raw_retry})")
            raise IngestionError(
                " ".join(parts),
                error_code=code,
                url=url,
            ) from exc
        elif exc.code == 404:
            code = "not_found"
        else:
            code = "http_error"
        raise IngestionError(
            f"HTTP {exc.code} from {url}",
            error_code=code,
            url=url,
        ) from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        is_timeout = isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower()
        raise IngestionError(
            f"Network error fetching {url}: {reason}",
            error_code="timeout" if is_timeout else "network_error",
            url=url,
        ) from exc
    if len(raw) > max_bytes:
        raise IngestionError(
            f"Response exceeds {max_bytes} bytes",
            error_code="response_too_large",
            url=url,
        )
    if encoding in ("gzip", "deflate"):
        raw = _decompress_bounded(raw, encoding, max_decompressed_bytes, url)
    return FetchResult(
        status=int(status),
        headers=headers,
        body=raw.decode("utf-8", errors="replace"),
    )


# =============================================================================
# Prompt-injection defense: nonce-delimited wrapping
# =============================================================================

def _wrap_fetched_content(text: str) -> str:
    """Wrap fetched content in delimiters with a per-request random nonce.

    Prevents trivial bypass via closing-tag injection — an attacker can't guess
    the nonce, and any close-tag that happens to collide is defensively escaped.
    """
    nonce = secrets.token_hex(8)
    tag_open = f"<fetched_job_description_v{nonce}>"
    tag_close = f"</fetched_job_description_v{nonce}>"
    safe_text = text.replace(tag_close, tag_close.replace(">", "&gt;"))
    return f"{tag_open}\n{safe_text}\n{tag_close}"


# =============================================================================
# Minimal HTML→text conversion (for raw_description rendering)
# =============================================================================

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    """Strip tags and collapse whitespace. Stdlib-only — no BeautifulSoup."""
    # Replace common block tags with line breaks before stripping
    html = re.sub(r"</?(p|br|li|div|h[1-6]|ul|ol)[^>]*>", "\n", html, flags=re.I)
    text = _HTML_TAG_RE.sub("", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Collapse horizontal whitespace but preserve newlines
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# =============================================================================
# Platform-specific fetchers
# =============================================================================

def _fetch_greenhouse(company: str, job_id: str) -> dict:
    """Greenhouse public Harvest Board API (no auth required)."""
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
    payload = json.loads(fetch(api_url).body)
    location_obj = payload.get("location") or {}
    comp = ""
    pay_ranges = payload.get("pay_input_ranges") or []
    if pay_ranges and isinstance(pay_ranges, list):
        comp = str(pay_ranges[0].get("text", ""))
    return {
        "title": str(payload.get("title", "")),
        "company": str(payload.get("company_name") or company),
        "location": str(location_obj.get("name", "")),
        "raw_description_html": str(payload.get("content", "")),
        "compensation": comp,
        "source": "greenhouse",
        "ingestion_method": "url_fetch_json",
    }


def _fetch_lever(company: str, job_id: str) -> dict:
    """Lever public postings API (no auth required)."""
    api_url = f"https://api.lever.co/v0/postings/{company}/{job_id}"
    payload = json.loads(fetch(api_url).body)
    categories = payload.get("categories") or {}
    return {
        "title": str(payload.get("text", "")),
        "company": company,
        "location": str(categories.get("location", "")),
        "raw_description_html": str(payload.get("descriptionPlain") or payload.get("description", "")),
        "source": "lever",
        "ingestion_method": "url_fetch_json",
    }


_INDEED_VIEWJOB_URL_RE = re.compile(r"https?://(?:www\.)?indeed\.com/viewjob")


def _fetch_indeed_viewjob(url: str, html_text: str | None = None) -> dict:
    """Indeed viewjob pages — extract from the embedded JobPosting JSON-LD.

    The rendered page is ~750KB of React chrome; the ``<main>`` tag wraps
    the entire app, so ``_fetch_generic_html`` pulls the whole page as the
    description and poisons skills/keyword extraction. JSON-LD is stable
    across Indeed's DOM reshuffles and carries the full posting schema.
    """
    if html_text is None:
        html_text = fetch(url).body
    title = ""
    company = ""
    location = ""
    description_html = ""
    compensation = ""
    employment_type = ""
    for match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text,
        re.I | re.S,
    ):
        try:
            node = json.loads(match.group(1).strip())
        except ValueError:
            continue
        if not isinstance(node, dict) or node.get("@type") != "JobPosting":
            continue
        title = str(node.get("title") or "").strip()
        org = node.get("hiringOrganization")
        if isinstance(org, dict):
            company = str(org.get("name") or "").strip()
        loc_node = node.get("jobLocation")
        if isinstance(loc_node, list):
            loc_node = loc_node[0] if loc_node else None
        if isinstance(loc_node, dict):
            addr = loc_node.get("address")
            if isinstance(addr, dict):
                parts = [
                    str(addr.get(k) or "").strip()
                    for k in ("addressLocality", "addressRegion", "addressCountry")
                ]
                location = ", ".join(p for p in parts if p)
        if not location and node.get("jobLocationType") == "TELECOMMUTE":
            location = "Remote"
        description_html = str(node.get("description") or "")
        base_salary = node.get("baseSalary")
        if isinstance(base_salary, dict):
            value = base_salary.get("value") or {}
            if isinstance(value, dict):
                lo = value.get("minValue")
                hi = value.get("maxValue")
                unit = value.get("unitText") or ""
                currency = base_salary.get("currency") or ""
                if lo and hi:
                    compensation = f"{currency} {lo}–{hi} {unit}".strip()
                elif lo:
                    compensation = f"{currency} {lo} {unit}".strip()
        employment_type = str(node.get("employmentType") or "")
        break
    if not description_html:
        # JSON-LD missing: prefer the #jobDescriptionText container over the
        # full page. This keeps the page chrome out of raw_description.
        m = re.search(
            r'<div[^>]*id="jobDescriptionText"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            html_text,
            re.I | re.S,
        )
        if m:
            description_html = m.group(1)
    return {
        "title": title,
        "company": company,
        "location": location,
        "compensation": compensation,
        "employment_type": employment_type,
        "raw_description_html": description_html,
        "source": "indeed",
        "ingestion_method": "url_fetch_jsonld",
    }


def _fetch_generic_html(url: str, html_text: str | None = None) -> dict:
    """Fallback for Ashby, Workday, company career pages.

    Low-trust — marks provenance as weak_inference and adds ingestion_notes
    telling the user to verify extracted fields manually.
    """
    if html_text is None:
        html_text = fetch(url).body
    # Extract <title> as best-guess title
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    title = m.group(1).strip() if m else ""
    # Prefer text inside <main> or <article> if present
    main_match = re.search(
        r"<(?:main|article)[^>]*>(.*?)</(?:main|article)>", html_text, re.I | re.S,
    )
    body_html = main_match.group(1) if main_match else html_text
    description = _html_to_text(body_html)
    # Company / location are not reliably extractable from arbitrary HTML
    parsed = urllib.parse.urlsplit(url)
    company_guess = (parsed.hostname or "").split(".")[0]
    return {
        "title": title,
        "company": company_guess,
        "location": "",
        "raw_description_html": description,
        "source": "html_fallback",
        "ingestion_method": "url_fetch_fallback",
        "ingestion_notes": "Extracted via generic HTML parser; verify fields manually.",
    }


# =============================================================================
# Markdown frontmatter synthesis — feeds extract_lead
# =============================================================================

def _to_markdown_with_frontmatter(fetched: dict) -> str:
    """Emit a markdown file with YAML frontmatter that extract_lead can consume.

    Reusing extract_lead means ingestion never diverges from manual extraction —
    one canonical code path for parsing requirements and keywords.
    """
    def yaml_escape(value: str) -> str:
        v = str(value).replace('"', '\\"')
        return f'"{v}"'

    lines = ["---"]
    for key in (
        "source", "company", "title", "location", "application_url",
        "canonical_url", "compensation", "employment_type",
        "ingestion_method", "ingested_at", "ingestion_notes",
    ):
        val = fetched.get(key)
        if val:
            lines.append(f"{key}: {yaml_escape(val)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {fetched.get('title', 'Untitled')}")
    lines.append("")
    desc = fetched.get("raw_description_html", "")
    # If the payload looks HTML-ish, convert to text
    if "<" in desc and ">" in desc:
        desc = _html_to_text(desc)
    lines.append(desc)
    return "\n".join(lines)


# =============================================================================
# Public entry points
# =============================================================================

def ingest_url(
    url: str,
    output_dir: Path,
    html_override: str | None = None,
) -> dict:
    """Fetch + canonicalize + write a lead via extract_lead.

    Intake lifecycle (data integrity):
    - Phase A: fetch + write intake markdown to _intake/pending/<hash>.md.
      Failures here move intake (if written) to _intake/failed/ with .err sidecar.
    - Phase B: call extract_lead. Failures here move intake to failed/.
    - Phase C: rename intake to _intake/processed/<lead_id>.md. Failures here
      log a warning but do NOT treat as failure (lead JSON is already persisted).

    Raises IngestionError with structured error_code on real failures.
    """
    if is_hard_fail_url(url):
        raise IngestionError(
            f"URL is behind a login wall and cannot be auto-ingested: {url}",
            error_code="login_wall",
            url=url,
            remediation=(
                "Paste the job description into a markdown file manually, "
                "then run `extract-lead --input <file>`."
            ),
        )
    canonical = canonicalize_url(url)
    intake_root = output_dir / "_intake"
    pending_dir = intake_root / "pending"
    processed_dir = intake_root / "processed"
    failed_dir = intake_root / "failed"
    for d in (pending_dir, processed_dir, failed_dir):
        d.mkdir(parents=True, exist_ok=True)

    intake_hash = short_hash(canonical)
    intake_path = pending_dir / f"{intake_hash}.md"

    try:
        if html_override is None:
            if m := GREENHOUSE_URL_RE.match(url):
                fetched = _fetch_greenhouse(m["company"], m["job_id"])
            elif m := LEVER_URL_RE.match(url):
                fetched = _fetch_lever(m["company"], m["job_id"])
            elif _INDEED_VIEWJOB_URL_RE.match(url):
                fetched = _fetch_indeed_viewjob(url)
            else:
                fetched = _fetch_generic_html(url)
        else:
            if _INDEED_VIEWJOB_URL_RE.match(url):
                fetched = _fetch_indeed_viewjob(url, html_text=html_override)
            else:
                fetched = _fetch_generic_html(url, html_text=html_override)
        fetched["application_url"] = url
        fetched["canonical_url"] = canonical
        fetched["ingested_at"] = now_iso()
        # Wrap fetched description in nonce-delimited tags for prompt-injection defense
        fetched["raw_description_html"] = _wrap_fetched_content(
            fetched.get("raw_description_html", "")
        )

        lead_md = _to_markdown_with_frontmatter(fetched)
        intake_path.write_text(lead_md, encoding="utf-8")

        # Delegate to extract_lead — one canonical parsing path.
        from .core import extract_lead
        lead = extract_lead(intake_path, output_dir)
    except Exception as exc:
        # Phase A or B failed — move to failed/ with sanitized error context
        if intake_path.exists():
            ts = now_iso().replace(":", "").replace("-", "")[:15]
            failed_path = failed_dir / f"{ts}-{intake_hash}.md"
            try:
                intake_path.replace(failed_path)
                failed_path.with_suffix(".err").write_text(
                    f"URL: {_sanitize_url_for_logging(url)}\n"
                    f"canonical: {_sanitize_url_for_logging(canonical)}\n"
                    f"error: {_sanitize_url_for_logging(str(exc))}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass  # Best-effort failure bookkeeping
        raise

    # Phase C: bookkeeping — best-effort rename. Failure here does NOT fake a failure.
    try:
        intake_path.replace(processed_dir / f"{lead['lead_id']}.md")
    except OSError as exc:
        logger.warning(
            "Post-ingest intake rename failed for lead %s: %s (lead JSON is persisted)",
            lead.get("lead_id"), exc,
        )
    return lead


def ingest_urls_file(
    urls_file: Path,
    output_dir: Path,
    max_workers: int = 5,
) -> dict:
    """Batch ingestion — parallelized + deduplicated.

    Dedup by canonical URL BEFORE dispatch closes the same-fingerprint race
    where two URL variants (e.g. with/without utm_source) would write the same
    lead_id from different threads.

    Returns {"successes": [...], "failures": [...]} — never raises on per-URL
    failures, so batch mode always completes even if some URLs fail.
    """
    raw_urls = [
        line.strip() for line in urls_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    seen_canonical: set[str] = set()
    unique_urls: list[str] = []
    for url in raw_urls:
        try:
            canonical = canonicalize_url(url)
        except Exception:
            canonical = url
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        unique_urls.append(url)

    successes: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_url = {pool.submit(ingest_url, u, output_dir): u for u in unique_urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                successes.append(future.result())
            except IngestionError as exc:
                failures.append({
                    "url": _sanitize_url_for_logging(url),
                    **exc.to_dict(),
                })
            except Exception as exc:
                failures.append({
                    "url": _sanitize_url_for_logging(url),
                    "error_code": "unexpected",
                    "message": str(exc),
                    "remediation": "",
                })
    return {"successes": successes, "failures": failures}
