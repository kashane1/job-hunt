from __future__ import annotations

from dataclasses import replace

from .base import DiscoveryLowConfidenceEntry, DiscoveryPage


class GreenhouseDiscoveryProvider:
    name = "greenhouse"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        slug = getattr(company, "greenhouse", "")
        if not slug:
            return DiscoveryPage(entries=(), truncated=False)
        from ..discovery import discover_greenhouse_board

        entries, truncated = discover_greenhouse_board(slug, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)


class LeverDiscoveryProvider:
    name = "lever"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        slug = getattr(company, "lever", "")
        if not slug:
            return DiscoveryPage(entries=(), truncated=False)
        from ..discovery import discover_lever_board

        entries, truncated = discover_lever_board(slug, rate_limiter)
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)


class IndeedSearchDiscoveryProvider:
    name = "indeed_search"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        url = getattr(company, "indeed_search_url", "")
        if not url:
            return DiscoveryPage(entries=(), truncated=False)
        from ..indeed_discovery import discover_indeed_search

        entries, truncated = discover_indeed_search(url, rate_limiter)
        if watchlist_company:
            entries = [replace(entry, source_company=watchlist_company) for entry in entries]
        return DiscoveryPage(entries=tuple(entries), truncated=truncated)


class CareersDiscoveryProvider:
    name = "careers"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        careers_url = getattr(company, "careers_url", "")
        if not careers_url:
            return DiscoveryPage(entries=(), truncated=False)
        from ..discovery import discover_company_careers

        crawl = discover_company_careers(
            careers_url,
            rate_limiter,
            robots,
            watchlist_company=watchlist_company or getattr(company, "name", ""),
        )
        return DiscoveryPage(
            entries=tuple(crawl.high_confidence),
            truncated=False,
            ats_hits=tuple(crawl.ats_hits),
            low_confidence=tuple(
                DiscoveryLowConfidenceEntry(
                    candidate_url=item["candidate_url"],
                    anchor_text=item["anchor_text"],
                    signals=tuple(item["signals"]),
                    source_page=item["source_page"],
                )
                for item in crawl.low_confidence
            ),
        )


_PROVIDERS = {
    "greenhouse": GreenhouseDiscoveryProvider(),
    "lever": LeverDiscoveryProvider(),
    "indeed_search": IndeedSearchDiscoveryProvider(),
    "careers": CareersDiscoveryProvider(),
}


def get_discovery_provider(name: str):
    return _PROVIDERS.get(name)

