"""Active job discovery — Greenhouse/Lever board APIs + generic careers crawl.

Batch 3 (2026-04-16).

This module contributes orchestration and URL generation only. Every HTTP
request goes through `ingestion.fetch()`. Every lead write goes through
`ingestion.ingest_url()`. SSRF posture, decompression safety, prompt-injection
defense, intake lifecycle, and canonicalization stay in one place.

Phase layout (this file grows over batch 3):
- Phase 2 (current): board listing fetchers + type skeleton
- Phase 3: generic career-page crawler (JSON-LD → ATS subdomain → heuristic)
- Phase 4: `discover_jobs` orchestration + CLI helpers
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal

from .ingestion import (
    FetchResult,
    GREENHOUSE_URL_RE,
    IngestionError,
    LEVER_URL_RE,
    fetch,
)
from .net_policy import DomainRateLimiter
from .utils import StructuredError, now_iso

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

DISCOVERY_USER_AGENT: Final = "job-hunt/0.3"

MAX_LISTING_BYTES: Final = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES: Final = 20_000_000
MAX_WATCHLIST_COMPANIES: Final = 200
FETCH_CHAIN_TIMEOUT_S: Final = 20

COMPANY_NAME_RE: Final = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
ENTRY_ID_RE: Final = re.compile(r"^[a-f0-9]{16}$")

CURSOR_KEY_SEPARATOR: Final = "|"

DISCOVERY_ERROR_CODES: Final = frozenset({
    "unknown_platform",
    "hard_fail_platform",
    "robots_fetch_failed",
    "watchlist_invalid",
    "watchlist_entry_exists",
    "watchlist_comments_present",
    "cursor_corrupt",
    "cursor_tuple_not_found",
    "review_entry_not_found",
    "anti_bot_blocked",
    "review_schema_invalid",
    "lead_write_race",
})


class DiscoveryError(StructuredError):
    """Structured error for discovery-specific failures."""

    ALLOWED_ERROR_CODES = DISCOVERY_ERROR_CODES


SOURCE_NAME_MAP: Final = {
    "greenhouse": ("greenhouse", "greenhouse_board"),
    "lever": ("lever", "lever_board"),
    "careers": ("careers_html", "careers_html"),
}


# =============================================================================
# Data types — explicit to_dict so schemas align with runtime shape
# =============================================================================

Confidence = Literal["high", "weak_inference"]
Bucket = Literal[
    "discovered",
    "filtered_out",
    "duplicate_within_run",
    "already_known",
    "skipped_by_robots",
    "skipped_by_budget",
    "failed",
    "low_confidence",
]

BUCKETS: Final = (
    "discovered",
    "filtered_out",
    "duplicate_within_run",
    "already_known",
    "skipped_by_robots",
    "skipped_by_budget",
    "failed",
    "low_confidence",
)


@dataclass(frozen=True)
class ListingEntry:
    title: str
    location: str
    posting_url: str
    source: str
    source_company: str
    internal_id: str
    updated_at: str
    signals: tuple[str, ...] = ()
    confidence: Confidence = "high"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "location": self.location,
            "posting_url": self.posting_url,
            "source": self.source,
            "source_company": self.source_company,
            "internal_id": self.internal_id,
            "updated_at": self.updated_at,
            "signals": list(self.signals),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Outcome:
    bucket: Bucket
    entry: ListingEntry | None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "entry": self.entry.to_dict() if self.entry else None,
            "detail": dict(self.detail),
        }


@dataclass
class SourceRun:
    company: str
    source: str
    started_at: str
    completed: bool
    listing_truncated: bool
    budget_exhausted: bool
    entry_count: int

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "source": self.source,
            "started_at": self.started_at,
            "completed": self.completed,
            "listing_truncated": self.listing_truncated,
            "budget_exhausted": self.budget_exhausted,
            "entry_count": self.entry_count,
        }


@dataclass
class DiscoveryResult:
    outcomes: list[Outcome]
    sources_run: list[SourceRun]
    run_started_at: str
    run_completed_at: str

    def by_bucket(self, bucket: Bucket) -> list[Outcome]:
        return [o for o in self.outcomes if o.bucket == bucket]

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "run_started_at": self.run_started_at,
            "run_completed_at": self.run_completed_at,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "sources_run": [s.to_dict() for s in self.sources_run],
            "counts": {b: len(self.by_bucket(b)) for b in BUCKETS},
        }


# =============================================================================
# DiscoveryConfig — trims the orchestrator signature
# =============================================================================

@dataclass(frozen=True)
class DiscoveryConfig:
    max_ingest: int = 50
    max_workers: int = 3
    sources: tuple[str, ...] = ()
    dry_run: bool = False
    auto_score: bool = True
    score_concurrency: int = 3
    scoring_config: dict | None = None
    candidate_profile: dict | None = None
    reset_cursor: tuple[str, str] | None = None


# =============================================================================
# Board listing fetchers — Greenhouse + Lever
# =============================================================================

def _fetch_listing(url: str) -> FetchResult:
    """Wrapper around `ingestion.fetch()` with listing-sized ingress caps."""
    return fetch(
        url,
        timeout=FETCH_CHAIN_TIMEOUT_S,
        max_bytes=MAX_LISTING_BYTES,
        max_decompressed_bytes=MAX_LISTING_DECOMPRESSED_BYTES,
    )


def discover_greenhouse_board(
    company: str,
    rate_limiter: DomainRateLimiter,
) -> tuple[list[ListingEntry], bool]:
    """Fetch Greenhouse public board listings. Returns (entries, truncated).

    Uses `GET /v1/boards/{company}/jobs`. Unknown companies return 404 which
    surfaces as an `IngestionError(not_found)` — discovery callers translate
    that to an empty result, not a failure.
    """
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
    rate_limiter.acquire(api_url)
    try:
        result = _fetch_listing(api_url)
    except IngestionError as exc:
        if exc.error_code == "not_found":
            return [], False
        raise
    truncated = len(result.body) >= MAX_LISTING_BYTES - 1
    payload = json.loads(result.body)
    jobs = payload.get("jobs", []) or []
    entries: list[ListingEntry] = []
    for job in jobs:
        posting_url = str(job.get("absolute_url", ""))
        if not GREENHOUSE_URL_RE.match(posting_url):
            continue
        location_obj = job.get("location") or {}
        entries.append(ListingEntry(
            title=str(job.get("title", "")),
            location=str(location_obj.get("name", "")) if isinstance(location_obj, dict) else str(location_obj),
            posting_url=posting_url,
            source="greenhouse",
            source_company=company,
            internal_id=str(job.get("id", "")),
            updated_at=str(job.get("updated_at", "")),
            signals=(),
            confidence="high",
        ))
    return entries, truncated


def discover_lever_board(
    company: str,
    rate_limiter: DomainRateLimiter,
) -> tuple[list[ListingEntry], bool]:
    """Fetch Lever public postings listings. Returns (entries, truncated).

    Uses `GET /v0/postings/{company}?mode=json`. `createdAt` is a ms-epoch int;
    we convert to ISO-8601 so the shape matches Greenhouse.
    """
    api_url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    rate_limiter.acquire(api_url)
    try:
        result = _fetch_listing(api_url)
    except IngestionError as exc:
        if exc.error_code == "not_found":
            return [], False
        raise
    truncated = len(result.body) >= MAX_LISTING_BYTES - 1
    payload = json.loads(result.body)
    if not isinstance(payload, list):
        return [], truncated
    entries: list[ListingEntry] = []
    for posting in payload:
        if not isinstance(posting, dict):
            continue
        posting_url = str(posting.get("hostedUrl") or posting.get("applyUrl") or "")
        if not LEVER_URL_RE.match(posting_url):
            continue
        categories = posting.get("categories") or {}
        if isinstance(categories, dict):
            location = str(categories.get("location", ""))
        else:
            location = ""
        created = posting.get("createdAt")
        updated_iso = ""
        if isinstance(created, (int, float)):
            try:
                updated_iso = (
                    datetime.fromtimestamp(created / 1000.0, tz=UTC)
                    .replace(microsecond=0)
                    .isoformat()
                )
            except (OverflowError, ValueError, OSError):
                updated_iso = ""
        entries.append(ListingEntry(
            title=str(posting.get("text", "")),
            location=location,
            posting_url=posting_url,
            source="lever",
            source_company=company,
            internal_id=str(posting.get("id", "")),
            updated_at=updated_iso,
            signals=(),
            confidence="high",
        ))
    return entries, truncated
