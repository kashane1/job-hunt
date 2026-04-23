from __future__ import annotations

import re

_GREENHOUSE_RE = re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/", re.IGNORECASE)
_LEVER_RE = re.compile(r"^https?://(?:jobs|hire)\.lever\.co/", re.IGNORECASE)
_WORKDAY_RE = re.compile(r"^https?://[^/]+\.myworkdayjobs\.com/", re.IGNORECASE)
_ASHBY_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/", re.IGNORECASE)


def surface_for_external_url(url: str) -> str | None:
    if _GREENHOUSE_RE.search(url):
        return "greenhouse_redirect"
    if _LEVER_RE.search(url):
        return "lever_redirect"
    if _WORKDAY_RE.search(url):
        return "workday_redirect"
    if _ASHBY_RE.search(url):
        return "ashby_redirect"
    return None


def normalize_redirect_chain(chain: object) -> list[str]:
    if isinstance(chain, str):
        value = chain.strip()
        return [value] if value else []
    if isinstance(chain, list):
        return [str(item) for item in chain if str(item).strip()]
    return []


def final_reroute_target(final_url: str, redirect_chain: list[str]) -> tuple[str | None, str]:
    surface = surface_for_external_url(final_url)
    resolved_url = final_url
    if surface is not None:
        return surface, resolved_url
    for candidate in reversed(redirect_chain):
        surface = surface_for_external_url(candidate)
        if surface is not None:
            return surface, candidate
    return None, resolved_url
