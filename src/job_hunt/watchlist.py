"""Watchlist: load, validate, filter predicates, safe YAML write-back.

Data shape (narrow YAML subset — see `simple_yaml`):

    companies:
      - name: "ExampleCo"
        greenhouse: "exampleco"
        lever: "exampleco"
        careers_url: "https://exampleco.com/careers"
        notes: "primary target"

    filters:
      keywords_any: ["engineer", "developer"]
      keywords_none: ["clearance required"]
      locations_any: ["remote", "san diego"]
      seniority_any: ["senior", "staff"]

Security posture:
- Control chars rejected at the CLI input layer (`validate_cli_string`).
- `_emit_watchlist_yaml` double-quotes all string values.
- Existing-comment detection forces an explicit `--force` before write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable

from .discovery_providers.usajobs import (
    USAJobsSearchProfile,
    usajobs_readiness_state,
)
from .simple_yaml import (
    emit_watchlist_yaml,
    has_comments,
    loads as load_yaml,
)
from .utils import ensure_dir


# =============================================================================
# Error codes — shared with discovery.py via DISCOVERY_ERROR_CODES.
# Because DiscoveryError is the user-facing raise type, we define the codes
# here too so watchlist validation can raise without a circular import.
# =============================================================================

WATCHLIST_ERROR_CODES: Final = frozenset({
    "watchlist_invalid",
    "watchlist_entry_exists",
    "watchlist_comments_present",
})


# =============================================================================
# Schema + filter semantics
# =============================================================================

COMPANY_NAME_RE: Final = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
ATS_SLUG_RE: Final = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
HTTPS_URL_RE: Final = re.compile(r"^https://")

MAX_COMPANIES: Final = 200
MAX_NOTES_LEN: Final = 1000

_FORBIDDEN_INPUT_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class WatchlistValidationError(ValueError):
    """Raised when a watchlist dict fails schema checks.

    Intentionally plain ValueError subclass — the CLI wraps it in a
    `DiscoveryError(watchlist_invalid)` so the user sees a structured
    stdout envelope.
    """


@dataclass(frozen=True)
class WatchlistFilters:
    keywords_any: tuple[str, ...] = ()
    keywords_none: tuple[str, ...] = ()
    locations_any: tuple[str, ...] = ()
    seniority_any: tuple[str, ...] = ()

    def passes(self, title: str, location: str) -> tuple[bool, str]:
        """Return (pass, reason). reason is empty when pass is True."""
        title_l = (title or "").casefold()
        location_l = (location or "").casefold()
        combined = f"{title_l} {location_l}"

        for bad in self.keywords_none:
            if bad.casefold() in combined:
                return False, f"matched keywords_none: {bad!r}"
        if self.keywords_any and not any(
            kw.casefold() in combined for kw in self.keywords_any
        ):
            return False, "no keywords_any matched"
        if self.locations_any and not any(
            loc.casefold() in location_l for loc in self.locations_any
        ):
            return False, "no locations_any matched"
        if self.seniority_any and not any(
            s.casefold() in title_l for s in self.seniority_any
        ):
            return False, "no seniority_any matched"
        return True, ""


@dataclass(frozen=True)
class WatchlistEntry:
    name: str
    greenhouse: str = ""
    lever: str = ""
    ashby: str = ""
    workable: str = ""
    careers_url: str = ""
    indeed_search_url: str = ""
    usajobs_search_profile: str = ""
    usajobs_profile: USAJobsSearchProfile | None = None
    notes: str = ""

    def has_source(self) -> bool:
        return bool(
            self.greenhouse
            or self.lever
            or self.ashby
            or self.workable
            or self.careers_url
            or self.indeed_search_url
            or self.usajobs_search_profile
        )


@dataclass(frozen=True)
class Watchlist:
    companies: tuple[WatchlistEntry, ...]
    filters: WatchlistFilters
    usajobs_profiles: tuple[USAJobsSearchProfile, ...] = ()


# =============================================================================
# Validation
# =============================================================================

def validate_cli_string(value: str, field_name: str) -> str:
    if _FORBIDDEN_INPUT_CHARS_RE.search(value or ""):
        raise WatchlistValidationError(
            f"Control character in {field_name!r}: {value!r}"
        )
    return value


def _as_str_tuple(values) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise WatchlistValidationError(f"expected list, got {type(values).__name__}")
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            raise WatchlistValidationError(f"non-string filter item: {v!r}")
        out.append(v)
    return tuple(out)


def _validate_entry(raw: dict) -> WatchlistEntry:
    if not isinstance(raw, dict):
        raise WatchlistValidationError(f"entry must be a mapping, got {type(raw).__name__}")
    name = raw.get("name")
    if not isinstance(name, str) or not COMPANY_NAME_RE.match(name):
        raise WatchlistValidationError(
            f"invalid or missing 'name' (must match {COMPANY_NAME_RE.pattern}): {name!r}"
        )
    greenhouse = raw.get("greenhouse", "") or ""
    lever = raw.get("lever", "") or ""
    ashby = raw.get("ashby", "") or ""
    workable = raw.get("workable", "") or ""
    careers_url = raw.get("careers_url", "") or ""
    indeed_search_url = raw.get("indeed_search_url", "") or ""
    usajobs_search_profile = raw.get("usajobs_search_profile", "") or ""
    notes = raw.get("notes", "") or ""

    if greenhouse and not ATS_SLUG_RE.match(greenhouse):
        raise WatchlistValidationError(f"invalid greenhouse slug: {greenhouse!r}")
    if lever and not ATS_SLUG_RE.match(lever):
        raise WatchlistValidationError(f"invalid lever slug: {lever!r}")
    if ashby and not ATS_SLUG_RE.match(ashby):
        raise WatchlistValidationError(f"invalid ashby slug: {ashby!r}")
    if workable and not ATS_SLUG_RE.match(workable):
        raise WatchlistValidationError(f"invalid workable subdomain: {workable!r}")
    if careers_url and not HTTPS_URL_RE.match(careers_url):
        raise WatchlistValidationError(
            f"careers_url must be https://: {careers_url!r}"
        )
    if indeed_search_url:
        if not HTTPS_URL_RE.match(indeed_search_url):
            raise WatchlistValidationError(
                f"indeed_search_url must be https://: {indeed_search_url!r}"
            )
        # Keep validation narrow — the richer parser lives in
        # indeed_discovery.IndeedSearchConfig.from_url. Here we only gate
        # on the host so the watchlist doesn't accept arbitrary URLs that
        # would surface as confusing errors at discover-jobs time.
        if "indeed.com" not in indeed_search_url.lower():
            raise WatchlistValidationError(
                f"indeed_search_url must target indeed.com: {indeed_search_url!r}"
            )
    if usajobs_search_profile and not ATS_SLUG_RE.match(usajobs_search_profile):
        raise WatchlistValidationError(
            f"invalid usajobs_search_profile: {usajobs_search_profile!r}"
        )
    if len(notes) > MAX_NOTES_LEN:
        raise WatchlistValidationError(
            f"notes too long ({len(notes)} > {MAX_NOTES_LEN})"
        )
    entry = WatchlistEntry(
        name=name,
        greenhouse=greenhouse,
        lever=lever,
        ashby=ashby,
        workable=workable,
        careers_url=careers_url,
        indeed_search_url=indeed_search_url,
        usajobs_search_profile=usajobs_search_profile,
        notes=notes,
    )
    return entry


def _validate_usajobs_profile(raw: dict) -> USAJobsSearchProfile:
    if not isinstance(raw, dict):
        raise WatchlistValidationError(
            f"usajobs profile must be a mapping, got {type(raw).__name__}"
        )
    name = str(raw.get("name") or "")
    if not ATS_SLUG_RE.match(name):
        raise WatchlistValidationError(f"invalid usajobs profile name: {name!r}")
    results_per_page = raw.get("results_per_page", 25)
    if not isinstance(results_per_page, int) or not (1 <= results_per_page <= 500):
        raise WatchlistValidationError(
            f"usajobs results_per_page must be 1-500: {results_per_page!r}"
        )
    who_may_apply = str(raw.get("who_may_apply", "Public") or "Public")
    if who_may_apply not in {"All", "Public", "Status"}:
        raise WatchlistValidationError(
            f"usajobs who_may_apply must be All, Public, or Status: {who_may_apply!r}"
        )
    fields = str(raw.get("fields", "Full") or "Full")
    if fields not in {"Min", "Full"}:
        raise WatchlistValidationError(
            f"usajobs fields must be Min or Full: {fields!r}"
        )
    remote_indicator = raw.get("remote_indicator")
    if remote_indicator not in (None, True, False):
        raise WatchlistValidationError(
            f"usajobs remote_indicator must be true/false: {remote_indicator!r}"
        )
    date_posted = raw.get("date_posted")
    if date_posted is not None and (
        not isinstance(date_posted, int) or not (0 <= date_posted <= 60)
    ):
        raise WatchlistValidationError(
            f"usajobs date_posted must be 0-60 days: {date_posted!r}"
        )
    sort_direction = str(raw.get("sort_direction", "") or "")
    if sort_direction and sort_direction not in {"Asc", "Desc"}:
        raise WatchlistValidationError(
            f"usajobs sort_direction must be Asc or Desc: {sort_direction!r}"
        )
    return USAJobsSearchProfile(
        name=name,
        keyword=str(raw.get("keyword", "") or ""),
        location_name=str(raw.get("location_name", "") or ""),
        organization=str(raw.get("organization", "") or ""),
        results_per_page=results_per_page,
        who_may_apply=who_may_apply,
        remote_indicator=remote_indicator,
        fields=fields,
        date_posted=date_posted if isinstance(date_posted, int) else None,
        sort_field=str(raw.get("sort_field", "") or ""),
        sort_direction=sort_direction,
        job_category_code=str(raw.get("job_category_code", "") or ""),
        position_schedule_type_code=str(raw.get("position_schedule_type_code", "") or ""),
        position_offering_type_code=str(raw.get("position_offering_type_code", "") or ""),
        hiring_path=str(raw.get("hiring_path", "") or ""),
    )


def _validate_filters(raw) -> WatchlistFilters:
    if raw is None:
        return WatchlistFilters()
    if not isinstance(raw, dict):
        raise WatchlistValidationError("'filters' must be a mapping")
    return WatchlistFilters(
        keywords_any=_as_str_tuple(raw.get("keywords_any")),
        keywords_none=_as_str_tuple(raw.get("keywords_none")),
        locations_any=_as_str_tuple(raw.get("locations_any")),
        seniority_any=_as_str_tuple(raw.get("seniority_any")),
    )


def parse_watchlist(data: dict) -> Watchlist:
    if not isinstance(data, dict):
        raise WatchlistValidationError("watchlist root must be a mapping")
    raw_companies = data.get("companies")
    if not isinstance(raw_companies, list):
        raise WatchlistValidationError("missing or invalid 'companies' list")
    if len(raw_companies) > MAX_COMPANIES:
        raise WatchlistValidationError(
            f"too many companies ({len(raw_companies)} > {MAX_COMPANIES})"
        )
    raw_profiles = data.get("usajobs_profiles")
    profile_entries: list[USAJobsSearchProfile] = []
    profiles_by_name: dict[str, USAJobsSearchProfile] = {}
    if raw_profiles is not None:
        if not isinstance(raw_profiles, list):
            raise WatchlistValidationError("usajobs_profiles must be a list")
        for raw_profile in raw_profiles:
            profile = _validate_usajobs_profile(raw_profile)
            if profile.name in profiles_by_name:
                raise WatchlistValidationError(
                    f"duplicate usajobs profile name: {profile.name!r}"
                )
            profiles_by_name[profile.name] = profile
            profile_entries.append(profile)
    seen_names: set[str] = set()
    entries: list[WatchlistEntry] = []
    for raw in raw_companies:
        entry = _validate_entry(raw)
        if entry.name in seen_names:
            raise WatchlistValidationError(f"duplicate company name: {entry.name!r}")
        seen_names.add(entry.name)
        if entry.usajobs_search_profile:
            entry = WatchlistEntry(
                name=entry.name,
                greenhouse=entry.greenhouse,
                lever=entry.lever,
                ashby=entry.ashby,
                workable=entry.workable,
                careers_url=entry.careers_url,
                indeed_search_url=entry.indeed_search_url,
                usajobs_search_profile=entry.usajobs_search_profile,
                usajobs_profile=profiles_by_name.get(entry.usajobs_search_profile),
                notes=entry.notes,
            )
        entries.append(entry)
    filters = _validate_filters(data.get("filters"))
    return Watchlist(
        companies=tuple(entries),
        filters=filters,
        usajobs_profiles=tuple(profile_entries),
    )


def load_watchlist(path: Path) -> Watchlist:
    text = path.read_text(encoding="utf-8")
    try:
        data = load_yaml(text)
    except ValueError as exc:
        raise WatchlistValidationError(f"YAML parse error: {exc}") from exc
    return parse_watchlist(data)


def watchlist_to_dict(wl: Watchlist) -> dict:
    companies = []
    for c in wl.companies:
        entry: dict[str, str] = {"name": c.name}
        if c.greenhouse:
            entry["greenhouse"] = c.greenhouse
        if c.lever:
            entry["lever"] = c.lever
        if c.ashby:
            entry["ashby"] = c.ashby
        if c.workable:
            entry["workable"] = c.workable
        if c.careers_url:
            entry["careers_url"] = c.careers_url
        if c.indeed_search_url:
            entry["indeed_search_url"] = c.indeed_search_url
        if c.usajobs_search_profile:
            entry["usajobs_search_profile"] = c.usajobs_search_profile
        if c.notes:
            entry["notes"] = c.notes
        companies.append(entry)
    result: dict = {"companies": companies}
    f = wl.filters
    filters_dict: dict = {}
    if f.keywords_any:
        filters_dict["keywords_any"] = list(f.keywords_any)
    if f.keywords_none:
        filters_dict["keywords_none"] = list(f.keywords_none)
    if f.locations_any:
        filters_dict["locations_any"] = list(f.locations_any)
    if f.seniority_any:
        filters_dict["seniority_any"] = list(f.seniority_any)
    if filters_dict:
        result["filters"] = filters_dict
    if wl.usajobs_profiles:
        result["usajobs_profiles"] = [
            {
                "name": profile.name,
                **({
                    "keyword": profile.keyword,
                } if profile.keyword else {}),
                **({
                    "location_name": profile.location_name,
                } if profile.location_name else {}),
                **({
                    "organization": profile.organization,
                } if profile.organization else {}),
                "results_per_page": profile.results_per_page,
                "who_may_apply": profile.who_may_apply,
                "fields": profile.fields,
                **({
                    "remote_indicator": profile.remote_indicator,
                } if profile.remote_indicator is not None else {}),
                **({
                    "date_posted": profile.date_posted,
                } if profile.date_posted is not None else {}),
                **({
                    "sort_field": profile.sort_field,
                } if profile.sort_field else {}),
                **({
                    "sort_direction": profile.sort_direction,
                } if profile.sort_direction else {}),
                **({
                    "job_category_code": profile.job_category_code,
                } if profile.job_category_code else {}),
                **({
                    "position_schedule_type_code": profile.position_schedule_type_code,
                } if profile.position_schedule_type_code else {}),
                **({
                    "position_offering_type_code": profile.position_offering_type_code,
                } if profile.position_offering_type_code else {}),
                **({
                    "hiring_path": profile.hiring_path,
                } if profile.hiring_path else {}),
            }
            for profile in wl.usajobs_profiles
        ]
    return result


# =============================================================================
# Safe writes
# =============================================================================

def write_watchlist(path: Path, wl: Watchlist, *, force: bool = False) -> None:
    """Emit via `emit_watchlist_yaml` and replace atomically.

    Raises WatchlistValidationError('watchlist_comments_present') when the
    existing target file contains YAML comments and `force` is False. Comments
    are lost on round-trip because `simple_yaml` has no comment AST.
    """
    if path.exists() and not force:
        existing = path.read_text(encoding="utf-8")
        if has_comments(existing):
            raise WatchlistValidationError("watchlist_comments_present")

    ensure_dir(path.parent)
    payload = watchlist_to_dict(wl)
    text = emit_watchlist_yaml(payload)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# =============================================================================
# CRUD helpers — thin wrappers above load/parse/write
# =============================================================================

def watchlist_show(path: Path, company: str | None = None) -> dict:
    wl = load_watchlist(path)
    if company is None:
        return watchlist_to_dict(wl)
    for c in wl.companies:
        if c.name == company:
            return {"companies": [_entry_dict(c)]}
    raise WatchlistValidationError(f"company not found: {company!r}")


def _entry_dict(c: WatchlistEntry) -> dict:
    out: dict[str, str] = {"name": c.name}
    if c.greenhouse:
        out["greenhouse"] = c.greenhouse
    if c.lever:
        out["lever"] = c.lever
    if c.ashby:
        out["ashby"] = c.ashby
    if c.workable:
        out["workable"] = c.workable
    if c.careers_url:
        out["careers_url"] = c.careers_url
    if c.indeed_search_url:
        out["indeed_search_url"] = c.indeed_search_url
    if c.usajobs_search_profile:
        out["usajobs_search_profile"] = c.usajobs_search_profile
    if c.notes:
        out["notes"] = c.notes
    return out


def watchlist_add(
    path: Path,
    entry: dict,
    *,
    force: bool = False,
) -> Watchlist:
    new_entry = _validate_entry(entry)
    if path.exists():
        existing = load_watchlist(path)
    else:
        existing = Watchlist(companies=(), filters=WatchlistFilters(), usajobs_profiles=())
    if any(c.name == new_entry.name for c in existing.companies):
        raise WatchlistValidationError("watchlist_entry_exists")
    companies = existing.companies + (new_entry,)
    updated = Watchlist(
        companies=companies,
        filters=existing.filters,
        usajobs_profiles=existing.usajobs_profiles,
    )
    write_watchlist(path, updated, force=force)
    return updated


def watchlist_remove(
    path: Path,
    name: str,
    *,
    force: bool = False,
) -> Watchlist:
    existing = load_watchlist(path)
    remaining = tuple(c for c in existing.companies if c.name != name)
    if len(remaining) == len(existing.companies):
        raise WatchlistValidationError(f"company not found: {name!r}")
    updated = Watchlist(
        companies=remaining,
        filters=existing.filters,
        usajobs_profiles=existing.usajobs_profiles,
    )
    write_watchlist(path, updated, force=force)
    return updated


def watchlist_validate(path: Path) -> dict:
    """Return {valid, errors, warnings} for the CLI."""
    result: dict = {"valid": True, "errors": [], "warnings": [], "source_readiness": []}
    try:
        wl = load_watchlist(path)
    except WatchlistValidationError as exc:
        result["valid"] = False
        result["errors"].append(str(exc))
        return result
    except FileNotFoundError:
        result["valid"] = False
        result["errors"].append(f"watchlist not found: {path}")
        return result
    for c in wl.companies:
        if not c.has_source():
            result["warnings"].append(
                f"{c.name!r} has no greenhouse/lever/ashby/workable/"
                "careers_url/indeed_search_url/usajobs_search_profile "
                "source — will be skipped"
            )
        if c.usajobs_search_profile:
            state = usajobs_readiness_state(c.usajobs_profile)
            readiness = {
                "company": c.name,
                "source": "usajobs",
                "profile": c.usajobs_search_profile,
                "state": state,
            }
            result["source_readiness"].append(readiness)
            if state == "profile_missing":
                result["warnings"].append(
                    f"{c.name!r} references missing USAJOBS profile "
                    f"{c.usajobs_search_profile!r}"
                )
            elif state == "credentials_missing":
                result["warnings"].append(
                    f"{c.name!r} has a USAJOBS profile but missing local API credentials"
                )
    return result
