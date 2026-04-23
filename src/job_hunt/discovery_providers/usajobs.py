from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass

from .base import DiscoveryPage
from ..ingestion import IngestionError, fetch

USAJOBS_API_KEY_ENV = "USAJOBS_API_KEY"
USAJOBS_USER_AGENT_EMAIL_ENV = "USAJOBS_USER_AGENT_EMAIL"
USAJOBS_HOST = "data.usajobs.gov"

MAX_LISTING_BYTES = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000
FETCH_CHAIN_TIMEOUT_S = 20


@dataclass(frozen=True)
class USAJobsSearchProfile:
    name: str
    keyword: str = ""
    location_name: str = ""
    organization: str = ""
    results_per_page: int = 25
    who_may_apply: str = "Public"
    remote_indicator: bool | None = None
    fields: str = "Full"
    date_posted: int | None = None
    sort_field: str = ""
    sort_direction: str = ""
    job_category_code: str = ""
    position_schedule_type_code: str = ""
    position_offering_type_code: str = ""
    hiring_path: str = ""

    def page_url(self, page: int) -> str:
        params: list[tuple[str, str]] = []
        if self.keyword:
            params.append(("Keyword", self.keyword))
        if self.location_name:
            params.append(("LocationName", self.location_name))
        if self.organization:
            params.append(("Organization", self.organization))
        if self.job_category_code:
            params.append(("JobCategoryCode", self.job_category_code))
        if self.position_schedule_type_code:
            params.append(("PositionScheduleTypeCode", self.position_schedule_type_code))
        if self.position_offering_type_code:
            params.append(("PositionOfferingTypeCode", self.position_offering_type_code))
        if self.hiring_path:
            params.append(("HiringPath", self.hiring_path))
        if self.sort_field:
            params.append(("SortField", self.sort_field))
        if self.sort_direction:
            params.append(("SortDirection", self.sort_direction))
        params.append(("WhoMayApply", self.who_may_apply))
        params.append(("Fields", self.fields))
        params.append(("ResultsPerPage", str(self.results_per_page)))
        params.append(("Page", str(page)))
        if self.remote_indicator is not None:
            params.append(("RemoteIndicator", "True" if self.remote_indicator else "False"))
        if self.date_posted is not None:
            params.append(("DatePosted", str(self.date_posted)))
        return "https://data.usajobs.gov/api/Search?" + urllib.parse.urlencode(params)


def usajobs_credentials_present() -> bool:
    return bool(
        os.environ.get(USAJOBS_API_KEY_ENV, "").strip()
        and os.environ.get(USAJOBS_USER_AGENT_EMAIL_ENV, "").strip()
    )


def usajobs_readiness_state(profile: USAJobsSearchProfile | None) -> str:
    if profile is None:
        return "profile_missing"
    if not usajobs_credentials_present():
        return "credentials_missing"
    return "ready"


def discover_usajobs_profile(
    profile: USAJobsSearchProfile,
    rate_limiter,
    *,
    cursor: str | None = None,
):
    from ..discovery import DiscoveryError, ListingEntry

    state = usajobs_readiness_state(profile)
    if state == "profile_missing":
        raise DiscoveryError(
            "USAJOBS search profile is missing.",
            error_code="usajobs_profile_missing",
            remediation="Define the referenced profile in config/watchlist.yaml under usajobs_profiles.",
        )
    if state == "credentials_missing":
        raise DiscoveryError(
            "USAJOBS credentials are missing.",
            error_code="usajobs_credentials_missing",
            remediation=(
                f"Set {USAJOBS_API_KEY_ENV} and {USAJOBS_USER_AGENT_EMAIL_ENV} "
                "in the environment or a local ignored file."
            ),
        )

    page_number = 1
    if cursor:
        try:
            page_number = max(1, int(cursor))
        except ValueError:
            page_number = 1
    api_url = profile.page_url(page_number)
    rate_limiter.acquire(api_url)
    try:
        result = fetch(
            api_url,
            timeout=FETCH_CHAIN_TIMEOUT_S,
            max_bytes=MAX_LISTING_BYTES,
            max_decompressed_bytes=MAX_LISTING_DECOMPRESSED_BYTES,
            headers={
                "Host": USAJOBS_HOST,
                "User-Agent": os.environ.get(USAJOBS_USER_AGENT_EMAIL_ENV, "").strip(),
                "Authorization-Key": os.environ.get(USAJOBS_API_KEY_ENV, "").strip(),
                "Accept": "application/json",
            },
        )
    except IngestionError as exc:
        if exc.error_code == "http_error" and ("HTTP 401" in str(exc) or "HTTP 403" in str(exc)):
            raise DiscoveryError(
                "USAJOBS rejected the supplied API credentials.",
                error_code="usajobs_auth_invalid",
                remediation=(
                    f"Verify {USAJOBS_API_KEY_ENV} and {USAJOBS_USER_AGENT_EMAIL_ENV} "
                    "match the approved USAJOBS API registration."
                ),
            ) from exc
        raise

    payload = json.loads(result.body)
    search_result = payload.get("SearchResult", {}) if isinstance(payload, dict) else {}
    items = search_result.get("SearchResultItems", []) if isinstance(search_result, dict) else []
    if not isinstance(items, list):
        items = []

    user_area = payload.get("UserArea")
    if not isinstance(user_area, dict):
        user_area = search_result.get("UserArea", {}) if isinstance(search_result, dict) else {}
    number_of_pages = 1
    if isinstance(user_area, dict):
        try:
            number_of_pages = max(1, int(user_area.get("NumberOfPages") or 1))
        except (TypeError, ValueError):
            number_of_pages = 1

    entries: list[ListingEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        descriptor = item.get("MatchedObjectDescriptor") or {}
        if not isinstance(descriptor, dict):
            continue
        apply_uri = descriptor.get("ApplyURI") or []
        if isinstance(apply_uri, list) and apply_uri:
            apply_url = str(apply_uri[0] or "")
        else:
            apply_url = ""
        posting_url = str(descriptor.get("PositionURI") or apply_url or "")
        if not posting_url:
            continue
        entries.append(
            ListingEntry(
                title=str(descriptor.get("PositionTitle") or ""),
                location=str(descriptor.get("PositionLocationDisplay") or ""),
                posting_url=posting_url,
                source="usajobs",
                source_company=profile.name,
                internal_id=str(item.get("MatchedObjectId") or descriptor.get("PositionID") or ""),
                updated_at=str(
                    descriptor.get("PublicationStartDate")
                    or descriptor.get("PositionStartDate")
                    or ""
                ),
                signals=(),
                confidence="high",
                employer_name=str(descriptor.get("OrganizationName") or descriptor.get("DepartmentName") or ""),
            )
        )
    next_cursor = str(page_number + 1) if page_number < number_of_pages else None
    return entries, False, next_cursor


class USAJobsDiscoveryProvider:
    name = "usajobs"

    def list_entries(
        self,
        company: object,
        *,
        rate_limiter: object,
        robots: object | None = None,
        watchlist_company: str = "",
        cursor: str | None = None,
    ) -> DiscoveryPage:
        profile_name = getattr(company, "usajobs_search_profile", "")
        if not profile_name:
            return DiscoveryPage(entries=(), truncated=False)
        profile = getattr(company, "usajobs_profile", None)
        entries, truncated, next_cursor = discover_usajobs_profile(
            profile,
            rate_limiter,
            cursor=cursor,
        )
        return DiscoveryPage(
            entries=tuple(entries),
            truncated=truncated,
            next_cursor=next_cursor,
        )
