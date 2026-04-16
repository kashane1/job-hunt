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
import io
import ipaddress
import json
import logging
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Final

from .utils import now_iso, short_hash, slugify, write_json

logger = logging.getLogger(__name__)

INGESTION_ERROR_CODES: Final = frozenset({
    "login_wall", "scheme_blocked", "private_ip_blocked", "redirect_blocked",
    "rate_limited", "timeout", "not_found", "response_too_large",
    "decompression_bomb", "dns_failed", "http_error", "network_error",
    "invalid_url", "unexpected",
})


class IngestionError(ValueError):
    """Structured error for agent consumption.

    Inherits ValueError per batch 1 convention (like ValidationError in
    schema_checks). Batch 2 convention: structured error classes are used at
    I/O/CLI boundaries (ingestion, pdf_export). Internal logic modules
    (ats_check, analytics, tracking, generation) raise plain ValueError.
    """

    def __init__(
        self,
        message: str,
        error_code: str,
        url: str = "",
        remediation: str = "",
    ):
        super().__init__(message)
        assert error_code in INGESTION_ERROR_CODES, f"unknown error_code: {error_code}"
        self.error_code = error_code
        self.url = url
        self.remediation = remediation

    def to_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "url": self.url,
            "remediation": self.remediation,
        }


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
HARD_FAIL_URL_PATTERNS: Final = (
    re.compile(r"https?://(?:www\.)?linkedin\.com/jobs/"),
    re.compile(r"https?://(?:www\.)?indeed\.com/viewjob"),
)

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

def _validate_url_for_fetch(url: str) -> tuple[str, list[str]]:
    """SSRF guard — returns (hostname, list of resolved IPs).

    Blocks:
    - non-http(s) schemes
    - private/loopback/link-local/reserved/multicast/unspecified IPs (IPv4+IPv6)
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
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
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


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate each redirect target for SSRF. Cap at 3 hops.

    Wraps inner validation errors with error_code='redirect_blocked' so agents
    can distinguish direct rejections from rejections-via-redirect.
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


def _fetch(
    url: str,
    timeout: int = MAX_FETCH_TIMEOUT_S,
    max_bytes: int = MAX_FETCH_BYTES,
) -> str:
    """Stdlib HTTP GET with SSRF guards, timeout, size limits, and gzip handling."""
    _validate_url_for_fetch(url)
    opener = urllib.request.build_opener(_StrictRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "job-hunt-cli/0.2",
            "Accept": "application/json, text/html;q=0.9",
            "Accept-Encoding": "gzip, deflate, identity",
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
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
        raw = _decompress_bounded(raw, encoding, MAX_DECOMPRESSED_BYTES, url)
    return raw.decode("utf-8", errors="replace")


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
    payload = json.loads(_fetch(api_url))
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
    payload = json.loads(_fetch(api_url))
    categories = payload.get("categories") or {}
    return {
        "title": str(payload.get("text", "")),
        "company": company,
        "location": str(categories.get("location", "")),
        "raw_description_html": str(payload.get("descriptionPlain") or payload.get("description", "")),
        "source": "lever",
        "ingestion_method": "url_fetch_json",
    }


def _fetch_generic_html(url: str, html_text: str | None = None) -> dict:
    """Fallback for Ashby, Workday, company career pages.

    Low-trust — marks provenance as weak_inference and adds ingestion_notes
    telling the user to verify extracted fields manually.
    """
    if html_text is None:
        html_text = _fetch(url)
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
    for pattern in HARD_FAIL_URL_PATTERNS:
        if pattern.match(url):
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
            else:
                fetched = _fetch_generic_html(url)
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
