from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..discovery import ListingEntry


@dataclass(frozen=True)
class DiscoveryLowConfidenceEntry:
    candidate_url: str
    anchor_text: str
    signals: tuple[str, ...]
    source_page: str


@dataclass(frozen=True)
class DiscoveryPage:
    entries: tuple["ListingEntry", ...]
    truncated: bool
    next_cursor: str | None = None
    ats_hits: tuple[tuple[str, str], ...] = ()
    low_confidence: tuple[DiscoveryLowConfidenceEntry, ...] = ()


class DiscoveryProvider(Protocol):
    name: str

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage: ...
