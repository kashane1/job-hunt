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
    careers_url: str = ""
    notes: str = ""

    def has_source(self) -> bool:
        return bool(self.greenhouse or self.lever or self.careers_url)


@dataclass(frozen=True)
class Watchlist:
    companies: tuple[WatchlistEntry, ...]
    filters: WatchlistFilters


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
    careers_url = raw.get("careers_url", "") or ""
    notes = raw.get("notes", "") or ""

    if greenhouse and not ATS_SLUG_RE.match(greenhouse):
        raise WatchlistValidationError(f"invalid greenhouse slug: {greenhouse!r}")
    if lever and not ATS_SLUG_RE.match(lever):
        raise WatchlistValidationError(f"invalid lever slug: {lever!r}")
    if careers_url and not HTTPS_URL_RE.match(careers_url):
        raise WatchlistValidationError(
            f"careers_url must be https://: {careers_url!r}"
        )
    if len(notes) > MAX_NOTES_LEN:
        raise WatchlistValidationError(
            f"notes too long ({len(notes)} > {MAX_NOTES_LEN})"
        )
    entry = WatchlistEntry(
        name=name,
        greenhouse=greenhouse,
        lever=lever,
        careers_url=careers_url,
        notes=notes,
    )
    return entry


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
    seen_names: set[str] = set()
    entries: list[WatchlistEntry] = []
    for raw in raw_companies:
        entry = _validate_entry(raw)
        if entry.name in seen_names:
            raise WatchlistValidationError(f"duplicate company name: {entry.name!r}")
        seen_names.add(entry.name)
        entries.append(entry)
    filters = _validate_filters(data.get("filters"))
    return Watchlist(companies=tuple(entries), filters=filters)


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
        if c.careers_url:
            entry["careers_url"] = c.careers_url
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
    if c.careers_url:
        out["careers_url"] = c.careers_url
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
        existing = Watchlist(companies=(), filters=WatchlistFilters())
    if any(c.name == new_entry.name for c in existing.companies):
        raise WatchlistValidationError("watchlist_entry_exists")
    companies = existing.companies + (new_entry,)
    updated = Watchlist(companies=companies, filters=existing.filters)
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
    updated = Watchlist(companies=remaining, filters=existing.filters)
    write_watchlist(path, updated, force=force)
    return updated


def watchlist_validate(path: Path) -> dict:
    """Return {valid, errors, warnings} for the CLI."""
    result: dict = {"valid": True, "errors": [], "warnings": []}
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
                f"{c.name!r} has no greenhouse/lever/careers_url source — will be skipped"
            )
    return result
