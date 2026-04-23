from __future__ import annotations

import hashlib
import json
import urllib.parse

from .base import DiscoveryPage
from ..ingestion import IngestionError, canonicalize_url, fetch

MAX_LISTING_BYTES = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000
FETCH_CHAIN_TIMEOUT_S = 20


def _entry_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _workable_api_url(subdomain: str, *, details: bool) -> str:
    params = [("details", "true" if details else "false")]
    return (
        "https://www.workable.com/api/accounts/"
        f"{urllib.parse.quote(subdomain, safe='')}"
        f"?{urllib.parse.urlencode(params)}"
    )


def _location_string(job: dict) -> str:
    location = job.get("location") or {}
    if isinstance(location, dict):
        value = location.get("location_str")
        if isinstance(value, str) and value.strip():
            return value
        city = str(location.get("city") or "").strip()
        country = str(location.get("country") or "").strip()
        parts = [part for part in (city, country) if part]
        if parts:
            return ", ".join(parts)
    return str(job.get("location") or "")


def discover_workable_account(
    subdomain: str,
    rate_limiter,
):
    from ..discovery import ListingEntry

    api_url = _workable_api_url(subdomain, details=False)
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
            return [], False
        raise
    truncated = len(result.body) >= MAX_LISTING_BYTES - 1
    payload = json.loads(result.body)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    if not isinstance(jobs, list):
        return [], truncated
    entries: list[ListingEntry] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        posting_url = str(job.get("url") or job.get("shortlink") or "")
        if not posting_url:
            continue
        entries.append(
            ListingEntry(
                title=str(job.get("title") or ""),
                location=_location_string(job),
                posting_url=posting_url,
                source="workable",
                source_company=subdomain,
                internal_id=str(job.get("shortcode") or job.get("id") or _entry_id_from_url(posting_url)),
                updated_at=str(job.get("updated_at") or job.get("created_at") or ""),
                signals=(),
                confidence="high",
            )
        )
    return entries, truncated


def _workable_subdomain_from_url(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host.endswith(".workable.com"):
        return ""
    if host.startswith("www.") or host.startswith("apply."):
        return ""
    return host.split(".", 1)[0]


def fetch_workable_job(url: str) -> dict | None:
    subdomain = _workable_subdomain_from_url(url)
    if not subdomain:
        return None
    payload = json.loads(fetch(_workable_api_url(subdomain, details=True)).body)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    target = canonicalize_url(url)
    for job in jobs:
        if not isinstance(job, dict):
            continue
        for candidate_url in (
            str(job.get("url") or ""),
            str(job.get("application_url") or ""),
            str(job.get("shortlink") or ""),
        ):
            if candidate_url and canonicalize_url(candidate_url) == target:
                salary = job.get("salary") or {}
                compensation = ""
                if isinstance(salary, dict):
                    salary_from = salary.get("salary_from")
                    salary_to = salary.get("salary_to")
                    salary_currency = str(salary.get("salary_currency") or "").upper()
                    if salary_from and salary_to:
                        compensation = f"{salary_currency} {salary_from}-{salary_to}".strip()
                return {
                    "title": str(job.get("title") or ""),
                    "company": str(payload.get("name") or subdomain),
                    "location": _location_string(job),
                    "raw_description_html": str(job.get("description") or ""),
                    "compensation": compensation,
                    "employment_type": str(job.get("employment_type") or ""),
                    "source": "workable",
                    "ingestion_method": "url_fetch_json",
                }
    return None


class WorkableDiscoveryProvider:
    name = "workable"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        subdomain = getattr(company, "workable", "")
        if not subdomain:
            return DiscoveryPage(entries=(), truncated=False)
        entries, truncated = discover_workable_account(subdomain, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)
