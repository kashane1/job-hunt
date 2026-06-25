from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from .base import DiscoveryPage
from ..ingestion import IngestionError, fetch

MAX_LISTING_BYTES = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000
FETCH_CHAIN_TIMEOUT_S = 20

# Personio's standard international feed is .com; many DACH companies only
# publish on .jobs.personio.de. The watchlist value is just the company
# subdomain; we try .com first and fall back to .de only on a miss, so the
# steady state is a single request.
_TLDS = ("com", "de")

# Anti XML-entity-expansion (billion laughs / XXE) guard. expat (stdlib
# ElementTree) does not fetch external entities, but it does expand internal
# ones — a DOCTYPE/ENTITY-bearing feed is refused deterministically before it
# ever reaches the parser, mirroring the repo's decompression-bomb guard.
_DOCTYPE_RE = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _feed_url(company: str, tld: str) -> str:
    return f"https://{urllib.parse.quote(company, safe='')}.jobs.personio.{tld}/xml"


def _job_url(company: str, tld: str, job_id: str) -> str:
    return (
        f"https://{urllib.parse.quote(company, safe='')}"
        f".jobs.personio.{tld}/job/{urllib.parse.quote(job_id, safe='')}"
    )


def _parse_positions(body: str) -> list[ET.Element]:
    if _DOCTYPE_RE.search(body):
        raise IngestionError(
            "Personio feed contains a DOCTYPE/ENTITY declaration; refusing to "
            "parse (XML entity-expansion guard)",
            error_code="xml_entity_blocked",
        )
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise IngestionError(
            f"Personio feed is not well-formed XML: {exc}",
            error_code="unexpected",
        ) from exc
    return list(root.iter("position"))


def _fetch_feed(company: str, rate_limiter):
    """Return (positions, tld, truncated) trying .com then .de on a miss."""
    for tld in _TLDS:
        api_url = _feed_url(company, tld)
        rate_limiter.acquire(api_url)
        try:
            result = fetch(
                api_url,
                timeout=FETCH_CHAIN_TIMEOUT_S,
                max_bytes=MAX_LISTING_BYTES,
                max_decompressed_bytes=MAX_LISTING_DECOMPRESSED_BYTES,
            )
        except IngestionError as exc:
            if exc.error_code == "not_found":
                continue
            raise
        truncated = len(result.body) >= MAX_LISTING_BYTES - 1
        return _parse_positions(result.body), tld, truncated
    return [], _TLDS[0], False


def discover_personio_company(
    company: str,
    rate_limiter,
):
    from ..discovery import ListingEntry

    positions, tld, truncated = _fetch_feed(company, rate_limiter)
    entries: list[ListingEntry] = []
    for position in positions:
        job_id = (position.findtext("id") or "").strip()
        if not job_id:
            continue
        posting_url = _job_url(company, tld, job_id)
        office = (position.findtext("office") or "").strip()
        department = (position.findtext("department") or "").strip()
        employment_type = (position.findtext("employmentType") or "").strip()
        entries.append(
            ListingEntry(
                title=(position.findtext("name") or "").strip(),
                location=office,
                posting_url=posting_url,
                source="personio",
                source_company=company,
                internal_id=job_id,
                updated_at=(position.findtext("createdAt") or "").strip(),
                signals=tuple(
                    signal
                    for signal in (
                        f"department:{department.lower()}" if department else "",
                        f"employment:{employment_type.lower()}"
                        if employment_type
                        else "",
                    )
                    if signal
                ),
                confidence="high",
            )
        )
    return entries, truncated


def _description_html(position: ET.Element) -> str:
    parts: list[str] = []
    for desc in position.iter("jobDescription"):
        name = (desc.findtext("name") or "").strip()
        value = (desc.findtext("value") or "").strip()
        if name and value:
            parts.append(f"<h3>{name}</h3>\n{value}")
        elif value:
            parts.append(value)
    return "\n".join(parts)


_PERSONIO_HOST_RE = re.compile(
    r"^(?P<company>[^./]+)\.jobs\.personio\.(?P<tld>com|de)$", re.IGNORECASE
)


def fetch_personio_job(url: str) -> dict | None:
    parsed = urllib.parse.urlsplit(url)
    host_match = _PERSONIO_HOST_RE.match(parsed.hostname or "")
    if not host_match:
        return None
    id_match = re.search(r"/job/(\d+)", parsed.path)
    if not id_match:
        return None
    company = host_match["company"]
    tld = host_match["tld"].lower()
    target_id = id_match.group(1)
    result = fetch(_feed_url(company, tld))
    for position in _parse_positions(result.body):
        if (position.findtext("id") or "").strip() != target_id:
            continue
        return {
            "title": (position.findtext("name") or "").strip(),
            "company": (position.findtext("subcompany") or "").strip() or company,
            "location": (position.findtext("office") or "").strip(),
            "raw_description_html": _description_html(position),
            "employment_type": (position.findtext("employmentType") or "").strip(),
            "source": "personio",
            "ingestion_method": "url_fetch_json",
        }
    return None


class PersonioDiscoveryProvider:
    name = "personio"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        subdomain = getattr(company, "personio", "")
        if not subdomain:
            return DiscoveryPage(entries=(), truncated=False)
        entries, truncated = discover_personio_company(subdomain, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)
