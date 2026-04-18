"""Indeed search → listing-entry pipeline.

Batch 4 Phase 3. Contributes the ``indeed_search`` source token used by
``discovery.discover_jobs``. Reuses ``ingestion.fetch`` (now allowlisted via
``config/domain-allowlist.yaml``) for SSRF-safe network access, and
``discovery.detect_anti_bot`` for Cloudflare/Akamai short-circuit.

What this module does NOT do (deferred to Phase 4+ / spike #046):
- Drive the browser (that's the agent via Claude-in-Chrome MCP).
- Maintain Indeed login state (the Chrome profile owns sessions).
- Retry on anti-bot signals (fail fast with ``anti_bot_blocked`` and let the
  user escalate). Adaptive pacing lives in ``apply_batch``.
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Final

from .ingestion import FetchResult, fetch
from .net_policy import DomainRateLimiter

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

INDEED_SEARCH_URL_RE: Final = re.compile(
    r"^https?://(?:www\.|secure\.)?indeed\.com/jobs(?:\?.*)?$"
)
INDEED_POSTING_JK_RE: Final = re.compile(r"^[a-f0-9]{16}$")

# Indeed returns job cards that embed `jk=<16-hex>` either in the data-jk
# attribute or on the "apply" link. These regexes extract the key without
# committing to a specific DOM shape — the page markup has changed every
# year and will again.
_JK_DATA_ATTR_RE: Final = re.compile(r'data-jk="([a-f0-9]{16})"')
_JK_URL_PARAM_RE: Final = re.compile(r'[?&]jk=([a-f0-9]{16})')

# Heuristic title + location. Indeed's rendered card currently puts the
# clickable link first with `aria-label="full details of <title>"`, then a
# `<span id="jobTitle-{jk}">…</span>`, a `data-testid="company-name"` span,
# and a `data-testid="text-location"` div. We tolerate multiple shapes
# because Indeed re-shuffles markup roughly yearly.
_TITLE_RE: Final = re.compile(
    r'<span[^>]*id="jobTitle-[a-f0-9]{16}"[^>]*>(.*?)</span>'
    r'|aria-label="(?:full details of |)([^"]+?)"[^>]*class="[^"]*jcs-JobTitle'
    r'|<(?:h2|h3)[^>]*(?:class="jobTitle[^"]*"|data-testid="jobtitle")[^>]*>(.*?)</(?:h2|h3)>',
    re.IGNORECASE | re.DOTALL,
)
_LOCATION_RE: Final = re.compile(
    r'<div[^>]*data-testid="text-location"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_COMPANY_RE: Final = re.compile(
    r'<span[^>]*data-testid="company-name"[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)

_HTML_TAG_RE: Final = re.compile(r"<[^>]+>")

MAX_PAGES_PER_RUN: Final = 2         # hard cap on pagination — keeps us low
RESULTS_PER_PAGE: Final = 10         # Indeed default


# =============================================================================
# Search URL parsing
# =============================================================================

@dataclass(frozen=True)
class IndeedSearchConfig:
    """Parsed form of an ``https://indeed.com/jobs?q=…&l=…`` URL.

    ``raw_url`` preserves the original for canonical round-tripping; the
    structured fields are used to build paginated fetch URLs.
    """

    raw_url: str
    query: str
    location: str
    radius: int | None
    start: int

    @classmethod
    def from_url(cls, url: str) -> "IndeedSearchConfig":
        if not INDEED_SEARCH_URL_RE.match(url):
            raise ValueError(f"Not a recognisable Indeed search URL: {url!r}")
        parsed = urllib.parse.urlsplit(url)
        qs = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=False))
        radius = None
        if qs.get("radius"):
            try:
                radius = int(qs["radius"])
            except ValueError:
                radius = None
        start = 0
        if qs.get("start"):
            try:
                start = max(0, int(qs["start"]))
            except ValueError:
                start = 0
        return cls(
            raw_url=url,
            query=qs.get("q", ""),
            location=qs.get("l", ""),
            radius=radius,
            start=start,
        )

    def page_url(self, page_index: int) -> str:
        """Return the URL for the Nth page (zero-indexed). ``start`` advances
        in steps of ``RESULTS_PER_PAGE`` per Indeed's own pagination."""
        qs: list[tuple[str, str]] = []
        if self.query:
            qs.append(("q", self.query))
        if self.location:
            qs.append(("l", self.location))
        if self.radius is not None:
            qs.append(("radius", str(self.radius)))
        qs.append(("start", str(page_index * RESULTS_PER_PAGE)))
        return "https://www.indeed.com/jobs?" + urllib.parse.urlencode(qs)


# =============================================================================
# Parsing — JSON-LD first, heuristic fallback
# =============================================================================

@dataclass(frozen=True)
class IndeedJobPosting:
    jk: str
    title: str
    company: str
    location: str
    posting_url: str
    source_signal: str  # "json_ld" | "heuristic"


def _jsonld_from_html(body: str) -> list[dict]:
    """Extract every ``application/ld+json`` block as parsed JSON objects."""
    out: list[dict] = []
    for match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        body,
        re.IGNORECASE | re.DOTALL,
    ):
        payload = match.group(1).strip()
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            out.append(data)
    return out


def _posting_from_jsonld(node: dict) -> IndeedJobPosting | None:
    if node.get("@type") != "JobPosting":
        return None
    url = str(node.get("url") or node.get("@id") or "")
    jk = _extract_jk(url)
    if not jk:
        return None
    title = html_module.unescape(str(node.get("title") or "")).strip()
    company_node = node.get("hiringOrganization") or {}
    company = ""
    if isinstance(company_node, dict):
        company = html_module.unescape(str(company_node.get("name") or "")).strip()
    elif isinstance(company_node, str):
        company = html_module.unescape(company_node).strip()
    loc_node = node.get("jobLocation") or {}
    location = _location_from_jsonld_node(loc_node)
    if not title:
        return None
    return IndeedJobPosting(
        jk=jk,
        title=title,
        company=company,
        location=location,
        posting_url=url,
        source_signal="json_ld",
    )


def _location_from_jsonld_node(node) -> str:
    if isinstance(node, list):
        for n in node:
            result = _location_from_jsonld_node(n)
            if result:
                return result
        return ""
    if not isinstance(node, dict):
        return ""
    address = node.get("address") or node
    if isinstance(address, dict):
        locality = str(address.get("addressLocality", "") or "").strip()
        region = str(address.get("addressRegion", "") or "").strip()
        country = str(address.get("addressCountry", "") or "").strip()
        parts = [p for p in (locality, region, country) if p]
        return ", ".join(parts)
    return ""


def _extract_jk(url: str) -> str | None:
    match = _JK_URL_PARAM_RE.search(url)
    if match:
        return match.group(1)
    return None


def _strip_tags(fragment: str) -> str:
    text = _HTML_TAG_RE.sub(" ", fragment)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_search_results(body: str) -> list[IndeedJobPosting]:
    """Parse a rendered Indeed search page → list of postings.

    Resolution ladder:
    1. JSON-LD ``@type = JobPosting`` (most reliable, but increasingly rare
       on rendered search pages — Indeed ships server-rendered React now).
    2. Heuristic extraction of ``data-jk`` attributes paired with title /
       location / company from the surrounding card. Brittle; the spike
       todo (#046) should refine this against live fixtures.

    Deduplicates by ``jk``. Anti-bot detection happens at the fetch layer.
    """
    results: list[IndeedJobPosting] = []
    seen: set[str] = set()

    for node in _jsonld_from_html(body):
        posting = _posting_from_jsonld(node)
        if posting and posting.jk not in seen:
            seen.add(posting.jk)
            results.append(posting)

    # Heuristic pass — every data-jk attribute is a potential posting card.
    for match in _JK_DATA_ATTR_RE.finditer(body):
        jk = match.group(1)
        if jk in seen:
            continue
        # Look at a ~4KB window following the attribute to pull title /
        # location / company without committing to exact DOM shape.
        window = body[match.start() : match.start() + 4000]
        title_match = _TITLE_RE.search(window)
        loc_match = _LOCATION_RE.search(window)
        company_match = _COMPANY_RE.search(window)
        title = ""
        if title_match:
            title_raw = next((g for g in title_match.groups() if g), "")
            title = _strip_tags(title_raw)
        location = _strip_tags(loc_match.group(1)) if loc_match else ""
        company = _strip_tags(company_match.group(1)) if company_match else ""
        if not title:
            continue
        seen.add(jk)
        results.append(IndeedJobPosting(
            jk=jk,
            title=title,
            company=company,
            location=location,
            posting_url=f"https://www.indeed.com/viewjob?jk={jk}",
            source_signal="heuristic",
        ))

    return results


# =============================================================================
# Fetch with rate limiting + anti-bot short-circuit
# =============================================================================

def fetch_search_page(
    search_url: str,
    rate_limiter: DomainRateLimiter,
) -> FetchResult:
    """Fetch a single search-result page with rate limiting."""
    rate_limiter.acquire(search_url)
    return fetch(search_url, timeout=20, max_bytes=5_000_000, max_decompressed_bytes=20_000_000)


# =============================================================================
# Public entry point — discover_indeed_search
# =============================================================================

def discover_indeed_search(
    search_url: str,
    rate_limiter: DomainRateLimiter,
    *,
    result_cap: int = 20,
    max_pages: int = MAX_PAGES_PER_RUN,
) -> tuple[list, bool]:
    """Paginate an Indeed search URL; return (entries, truncated).

    Lazily imports ``ListingEntry`` and ``DiscoveryError`` from ``discovery``
    to avoid a circular import at module load (discovery.py imports from
    here for the ``indeed_search`` source dispatch).

    Fails fast on anti-bot signals — no retry. Pagination stops at the
    earlier of: ``result_cap`` postings, ``max_pages`` pages, or a page
    that returns zero postings.
    """
    from .discovery import DiscoveryError, ListingEntry, detect_anti_bot
    from .utils import now_iso

    config = IndeedSearchConfig.from_url(search_url)
    entries: list = []
    pages_fetched = 0
    truncated = False
    for page in range(max_pages):
        if len(entries) >= result_cap:
            truncated = True
            break
        url = config.page_url(page)
        result = fetch_search_page(url, rate_limiter)
        pages_fetched += 1
        if detect_anti_bot(result):
            raise DiscoveryError(
                f"Anti-bot challenge detected at {url}",
                error_code="anti_bot_blocked",
                url=url,
                remediation=(
                    "Stop this Indeed run; Indeed's Cloudflare-backed defense is "
                    "blocking automated search. Retry only after the residual cookie "
                    "clears (often several hours)."
                ),
            )
        postings = parse_search_results(result.body)
        if not postings:
            break
        for posting in postings:
            if len(entries) >= result_cap:
                truncated = True
                break
            entries.append(ListingEntry(
                title=posting.title,
                location=posting.location,
                posting_url=posting.posting_url,
                source="indeed_search",
                source_company=posting.company,
                internal_id=posting.jk,
                updated_at=now_iso(),
                signals=(f"indeed_{posting.source_signal}",),
                confidence="high" if posting.source_signal == "json_ld" else "weak_inference",
                employer_name=posting.company,
            ))
    return entries, truncated
