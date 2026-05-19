from __future__ import annotations

import hashlib
import json
import urllib.parse
from dataclasses import replace

from .base import DiscoveryPage
from ..ingestion import IngestionError, fetch

MAX_LISTING_BYTES = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000
FETCH_CHAIN_TIMEOUT_S = 20
# Remotive's feed is unauthenticated and can be large; cap the page so an
# aggregator query can't dominate a discovery run's ingest budget.
RESULT_LIMIT = 100


def _entry_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _search_url(query: str) -> str:
    params = urllib.parse.urlencode({"search": query, "limit": RESULT_LIMIT})
    return f"https://remotive.com/api/remote-jobs?{params}"


def discover_remotive_search(
    query: str,
    rate_limiter,
):
    from ..discovery import ListingEntry

    api_url = _search_url(query)
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
    if len(jobs) >= RESULT_LIMIT:
        truncated = True
    entries: list[ListingEntry] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        posting_url = str(job.get("url") or "")
        if not posting_url:
            continue
        category = str(job.get("category") or "")
        job_type = str(job.get("job_type") or "")
        entries.append(
            ListingEntry(
                title=str(job.get("title") or ""),
                location=str(job.get("candidate_required_location") or "Remote"),
                posting_url=posting_url,
                source="remotive",
                source_company="remotive",
                internal_id=str(job.get("id") or _entry_id_from_url(posting_url)),
                updated_at=str(job.get("publication_date") or ""),
                signals=tuple(
                    signal
                    for signal in (
                        f"category:{category.lower()}" if category else "",
                        f"job_type:{job_type.lower()}" if job_type else "",
                    )
                    if signal
                ),
                confidence="high",
                employer_name=str(job.get("company_name") or ""),
            )
        )
    return entries, truncated


class RemotiveDiscoveryProvider:
    name = "remotive"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        query = getattr(company, "remotive_search", "")
        if not query:
            return DiscoveryPage(entries=(), truncated=False)
        entries, truncated = discover_remotive_search(query, rate_limiter)
        if watchlist_company:
            entries = [
                replace(entry, source_company=watchlist_company) for entry in entries
            ]
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)
