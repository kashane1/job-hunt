from __future__ import annotations

import re

from .base import ApplicationTarget, BoardAdapter
from .indeed import IndeedBoardAdapter
from .linkedin import LinkedInBoardAdapter

_ADAPTERS: tuple[BoardAdapter, ...] = (
    LinkedInBoardAdapter(),
    IndeedBoardAdapter(),
)

_SURFACE_PLAYBOOKS = {
    "indeed_easy_apply": "playbooks/application/indeed-easy-apply.md",
    "indeed_external_redirect": "playbooks/application/indeed-easy-apply.md",
    "greenhouse_redirect": "playbooks/application/greenhouse-redirect.md",
    "lever_redirect": "playbooks/application/lever-redirect.md",
    "workday_redirect": "playbooks/application/workday-redirect.md",
    "ashby_redirect": "playbooks/application/ashby-redirect.md",
    "linkedin_easy_apply_assisted": "playbooks/application/linkedin-easy-apply-assisted.md",
}

_DIRECT_URL_SURFACES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/", re.IGNORECASE), "greenhouse_redirect"),
    (re.compile(r"^https?://jobs\.lever\.co/", re.IGNORECASE), "lever_redirect"),
    (re.compile(r"^https?://[^/]+\.myworkdayjobs\.com/", re.IGNORECASE), "workday_redirect"),
    (re.compile(r"^https?://jobs\.ashbyhq\.com/", re.IGNORECASE), "ashby_redirect"),
)


def get_board_adapter(lead: dict | None, url: str) -> BoardAdapter:
    for adapter in _ADAPTERS:
        if adapter.matches_lead_or_url(lead, url):
            return adapter
    return IndeedBoardAdapter()


def resolve_application_target(
    lead: dict | None,
    posting_url: str,
    *,
    apply_type: str | None = None,
) -> ApplicationTarget:
    adapter = get_board_adapter(lead, posting_url)
    if isinstance(adapter, IndeedBoardAdapter) and posting_url:
        for pattern, surface in _DIRECT_URL_SURFACES:
            if pattern.search(posting_url):
                return ApplicationTarget(
                    origin_board=str((lead or {}).get("origin_board") or "unknown"),
                    surface=surface,
                    playbook_path=playbook_for_surface(surface),
                    surface_policy="browser_automated_human_submit",
                    correlation_keys_patch={},
                    batch_eligible=True,
                    handoff_kind="automation_playbook",
                    executor_backend="claude_chrome",
                )
    return adapter.resolve_application_target(lead or {}, posting_url=posting_url, apply_type=apply_type)


def playbook_for_surface(surface: str) -> str:
    return _SURFACE_PLAYBOOKS.get(surface, _SURFACE_PLAYBOOKS["indeed_easy_apply"])
