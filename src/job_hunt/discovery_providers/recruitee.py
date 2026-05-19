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


def _offers_url(company: str) -> str:
    return (
        f"https://{urllib.parse.quote(company, safe='')}.recruitee.com/api/offers/"
    )


def _location_string(offer: dict) -> str:
    explicit = str(offer.get("location") or "").strip()
    if explicit:
        return explicit
    city = str(offer.get("city") or "").strip()
    country = str(offer.get("country_code") or "").strip().upper()
    parts = [part for part in (city, country) if part]
    if parts:
        return ", ".join(parts)
    if offer.get("remote"):
        return "Remote"
    return ""


def _offer_url(offer: dict) -> str:
    return str(
        offer.get("careers_url")
        or offer.get("careers_apply_url")
        or offer.get("url")
        or ""
    )


def discover_recruitee_account(
    company: str,
    rate_limiter,
):
    from ..discovery import ListingEntry

    api_url = _offers_url(company)
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
    offers = payload.get("offers", []) if isinstance(payload, dict) else []
    if not isinstance(offers, list):
        return [], truncated
    entries: list[ListingEntry] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if str(offer.get("status") or "").lower() not in ("", "published"):
            continue
        posting_url = _offer_url(offer)
        if not posting_url:
            continue
        entries.append(
            ListingEntry(
                title=str(offer.get("title") or ""),
                location=_location_string(offer),
                posting_url=posting_url,
                source="recruitee",
                source_company=company,
                internal_id=str(offer.get("id") or offer.get("slug") or _entry_id_from_url(posting_url)),
                updated_at=str(offer.get("published_at") or offer.get("created_at") or ""),
                signals=tuple(
                    signal
                    for signal in (
                        f"department:{str(offer.get('department') or '').lower()}"
                        if offer.get("department")
                        else "",
                        f"employment:{str(offer.get('employment_type_code') or '').lower()}"
                        if offer.get("employment_type_code")
                        else "",
                        "remote" if offer.get("remote") else "",
                    )
                    if signal
                ),
                confidence="high",
            )
        )
    return entries, truncated


def _recruitee_subdomain_from_url(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host.endswith(".recruitee.com"):
        return ""
    if host.startswith("www.") or host.startswith("api."):
        return ""
    return host.split(".", 1)[0]


def fetch_recruitee_job(url: str) -> dict | None:
    company = _recruitee_subdomain_from_url(url)
    if not company:
        return None
    payload = json.loads(fetch(_offers_url(company)).body)
    offers = payload.get("offers", []) if isinstance(payload, dict) else []
    target = canonicalize_url(url)
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        for candidate_url in (
            str(offer.get("careers_url") or ""),
            str(offer.get("careers_apply_url") or ""),
            str(offer.get("url") or ""),
        ):
            if candidate_url and canonicalize_url(candidate_url) == target:
                description = "\n".join(
                    part
                    for part in (
                        str(offer.get("description") or ""),
                        str(offer.get("requirements") or ""),
                    )
                    if part
                )
                return {
                    "title": str(offer.get("title") or ""),
                    "company": str(offer.get("company_name") or company),
                    "location": _location_string(offer),
                    "raw_description_html": description,
                    "employment_type": str(offer.get("employment_type_code") or ""),
                    "source": "recruitee",
                    "ingestion_method": "url_fetch_json",
                }
    return None


class RecruiteeDiscoveryProvider:
    name = "recruitee"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        subdomain = getattr(company, "recruitee", "")
        if not subdomain:
            return DiscoveryPage(entries=(), truncated=False)
        entries, truncated = discover_recruitee_account(subdomain, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)
