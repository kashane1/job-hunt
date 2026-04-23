from __future__ import annotations

import hashlib
import json
import urllib.parse

from .base import DiscoveryPage
from ..ingestion import IngestionError, fetch

MAX_LISTING_BYTES = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000
FETCH_CHAIN_TIMEOUT_S = 20


def _entry_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def discover_ashby_board(
    slug: str,
    rate_limiter,
):
    from ..discovery import ListingEntry

    api_url = (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{urllib.parse.quote(slug, safe='')}"
        "?includeCompensation=false"
    )
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
        if job.get("isListed") is False:
            continue
        posting_url = str(job.get("jobUrl") or "")
        if not posting_url:
            continue
        location = str(job.get("location") or "")
        workplace_type = str(job.get("workplaceType") or "")
        if not location and workplace_type == "Remote":
            location = "Remote"
        entries.append(
            ListingEntry(
                title=str(job.get("title") or ""),
                location=location,
                posting_url=posting_url,
                source="ashby",
                source_company=slug,
                internal_id=_entry_id_from_url(posting_url),
                updated_at=str(job.get("publishedAt") or ""),
                signals=(
                    f"workplace_type:{workplace_type.lower()}" if workplace_type else "",
                    "remote" if job.get("isRemote") else "",
                ),
                confidence="high",
            )
        )
    cleaned = [
        ListingEntry(
            title=item.title,
            location=item.location,
            posting_url=item.posting_url,
            source=item.source,
            source_company=item.source_company,
            internal_id=item.internal_id,
            updated_at=item.updated_at,
            signals=tuple(signal for signal in item.signals if signal),
            confidence=item.confidence,
        )
        for item in entries
    ]
    return cleaned, truncated


def fetch_ashby_job(company: str, job_id: str) -> dict | None:
    api_url = (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{urllib.parse.quote(company, safe='')}"
        "?includeCompensation=false"
    )
    payload = json.loads(fetch(api_url).body)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_url = str(job.get("jobUrl") or "")
        apply_url = str(job.get("applyUrl") or "")
        if job_id not in job_url and job_id not in apply_url:
            continue
        return {
            "title": str(job.get("title") or ""),
            "company": company,
            "location": str(job.get("location") or ("Remote" if job.get("isRemote") else "")),
            "raw_description_html": str(job.get("descriptionHtml") or job.get("descriptionPlain") or ""),
            "employment_type": str(job.get("employmentType") or ""),
            "source": "ashby",
            "ingestion_method": "url_fetch_json",
        }
    return None


class AshbyDiscoveryProvider:
    name = "ashby"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        slug = getattr(company, "ashby", "")
        if not slug:
            return DiscoveryPage(entries=(), truncated=False)
        entries, truncated = discover_ashby_board(slug, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)
