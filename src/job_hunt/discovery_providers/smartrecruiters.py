from __future__ import annotations

import hashlib
import json
import urllib.parse

from .base import DiscoveryPage
from ..ingestion import IngestionError, fetch

MAX_LISTING_BYTES = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000
FETCH_CHAIN_TIMEOUT_S = 20
PAGE_LIMIT = 100
# Safety bound: SmartRecruiters caps `limit` at 100. 20 pages == 2000 postings
# is far past any single company's open-req count; if we hit it we stop and
# report truncated rather than walking an unbounded cursor.
MAX_PAGES = 20


def _entry_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _postings_url(company: str, *, offset: int) -> str:
    params = urllib.parse.urlencode({"limit": PAGE_LIMIT, "offset": offset})
    return (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{urllib.parse.quote(company, safe='')}/postings?{params}"
    )


def _seeker_url(company: str, posting_id: str) -> str:
    return (
        "https://jobs.smartrecruiters.com/"
        f"{urllib.parse.quote(company, safe='')}/{urllib.parse.quote(posting_id, safe='')}"
    )


def _location_string(posting: dict) -> str:
    location = posting.get("location") or {}
    if not isinstance(location, dict):
        return ""
    city = str(location.get("city") or "").strip()
    region = str(location.get("region") or "").strip()
    parts = [part for part in (city, region) if part]
    if parts:
        return ", ".join(parts)
    if location.get("remote"):
        return "Remote"
    return str(location.get("country") or "").strip()


def discover_smartrecruiters_company(
    company: str,
    rate_limiter,
):
    from ..discovery import ListingEntry

    entries: list[ListingEntry] = []
    offset = 0
    truncated = False
    for _page in range(MAX_PAGES):
        api_url = _postings_url(company, offset=offset)
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
                return entries, truncated
            raise
        if len(result.body) >= MAX_LISTING_BYTES - 1:
            truncated = True
        payload = json.loads(result.body)
        if not isinstance(payload, dict):
            break
        content = payload.get("content", [])
        if not isinstance(content, list) or not content:
            break
        for posting in content:
            if not isinstance(posting, dict):
                continue
            posting_id = str(posting.get("id") or "")
            if not posting_id:
                continue
            posting_url = _seeker_url(company, posting_id)
            employment = posting.get("typeOfEmployment") or {}
            experience = posting.get("experienceLevel") or {}
            entries.append(
                ListingEntry(
                    title=str(posting.get("name") or ""),
                    location=_location_string(posting),
                    posting_url=posting_url,
                    source="smartrecruiters",
                    source_company=company,
                    internal_id=posting_id,
                    updated_at=str(posting.get("releasedDate") or ""),
                    signals=tuple(
                        signal
                        for signal in (
                            f"employment:{str(employment.get('label') or '').lower()}"
                            if isinstance(employment, dict) and employment.get("label")
                            else "",
                            f"experience:{str(experience.get('label') or '').lower()}"
                            if isinstance(experience, dict) and experience.get("label")
                            else "",
                            "remote"
                            if isinstance(posting.get("location"), dict)
                            and posting["location"].get("remote")
                            else "",
                        )
                        if signal
                    ),
                    confidence="high",
                )
            )
        total = payload.get("totalFound")
        offset += len(content)
        if not isinstance(total, int) or offset >= total:
            break
    else:
        # Loop exhausted MAX_PAGES without breaking — more postings remain.
        truncated = True
    return entries, truncated


def _section_html(sections: dict, key: str) -> str:
    section = sections.get(key) if isinstance(sections, dict) else None
    if isinstance(section, dict):
        return str(section.get("text") or "")
    return ""


def fetch_smartrecruiters_job(company: str, job_id: str) -> dict | None:
    api_url = (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{urllib.parse.quote(company, safe='')}/postings/"
        f"{urllib.parse.quote(job_id, safe='')}"
    )
    try:
        payload = json.loads(fetch(api_url).body)
    except IngestionError as exc:
        if exc.error_code == "not_found":
            return None
        raise
    if not isinstance(payload, dict):
        return None
    job_ad = payload.get("jobAd") or {}
    sections = job_ad.get("sections") if isinstance(job_ad, dict) else {}
    description_html = "\n".join(
        html
        for html in (
            _section_html(sections, "companyDescription"),
            _section_html(sections, "jobDescription"),
            _section_html(sections, "qualifications"),
            _section_html(sections, "additionalInformation"),
        )
        if html
    )
    company_obj = payload.get("company") or {}
    employment = payload.get("typeOfEmployment") or {}
    return {
        "title": str(payload.get("name") or ""),
        "company": str(
            company_obj.get("name") if isinstance(company_obj, dict) else ""
        )
        or company,
        "location": _location_string(payload),
        "raw_description_html": description_html,
        "employment_type": str(
            employment.get("label") if isinstance(employment, dict) else ""
        ),
        "source": "smartrecruiters",
        "ingestion_method": "url_fetch_json",
    }


class SmartRecruitersDiscoveryProvider:
    name = "smartrecruiters"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        slug = getattr(company, "smartrecruiters", "")
        if not slug:
            return DiscoveryPage(entries=(), truncated=False)
        entries, truncated = discover_smartrecruiters_company(slug, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)
