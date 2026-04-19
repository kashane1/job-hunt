from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import ApplicationTarget

_LINKEDIN_URL_RE = re.compile(r"^https?://(?:[\w-]+\.)?linkedin\.com/", re.IGNORECASE)
_GREENHOUSE_RE = re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/", re.IGNORECASE)
_LEVER_RE = re.compile(r"^https?://jobs\.lever\.co/", re.IGNORECASE)
_WORKDAY_RE = re.compile(r"^https?://[^/]+\.myworkdayjobs\.com/", re.IGNORECASE)
_ASHBY_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/", re.IGNORECASE)


def _surface_for_external_url(url: str) -> str | None:
    if _GREENHOUSE_RE.search(url):
        return "greenhouse_redirect"
    if _LEVER_RE.search(url):
        return "lever_redirect"
    if _WORKDAY_RE.search(url):
        return "workday_redirect"
    if _ASHBY_RE.search(url):
        return "ashby_redirect"
    return None


class LinkedInBoardAdapter:
    name = "linkedin"

    def matches_lead_or_url(self, lead: dict | None, url: str) -> bool:
        source = str((lead or {}).get("source", ""))
        board = str((lead or {}).get("origin_board", ""))
        redirect_chain = (lead or {}).get("redirect_chain") or []
        return bool(
            _LINKEDIN_URL_RE.search(url)
            or source.startswith("linkedin")
            or board == self.name
            or any(_LINKEDIN_URL_RE.search(str(item)) for item in redirect_chain)
        )

    def resolve_application_target(
        self,
        lead: dict,
        *,
        posting_url: str,
        apply_type: str | None = None,
    ) -> ApplicationTarget:
        redirect_chain = [
            str(item) for item in (lead.get("redirect_chain") or []) if str(item).strip()
        ]
        final_url = str(
            lead.get("canonical_url")
            or lead.get("application_url")
            or lead.get("posting_url")
            or posting_url
        )
        ats_surface = _surface_for_external_url(final_url)
        if ats_surface is None and redirect_chain:
            ats_surface = _surface_for_external_url(redirect_chain[-1])
            if ats_surface is not None:
                final_url = redirect_chain[-1]

        if ats_surface is not None:
            playbook = {
                "greenhouse_redirect": "playbooks/application/greenhouse-redirect.md",
                "lever_redirect": "playbooks/application/lever-redirect.md",
                "workday_redirect": "playbooks/application/workday-redirect.md",
                "ashby_redirect": "playbooks/application/ashby-redirect.md",
            }[ats_surface]
            return ApplicationTarget(
                origin_board=self.name,
                surface=ats_surface,
                playbook_path=playbook,
                surface_policy="browser_automated_human_submit",
                correlation_keys_patch={
                    "origin_board": self.name,
                    "origin_posting_url": posting_url,
                    "posting_url": final_url,
                    "redirect_chain": redirect_chain,
                },
                batch_eligible=True,
                apply_host=urlparse(final_url).netloc.lower(),
                redirect_chain=redirect_chain,
                handoff_kind="automation_playbook",
                executor_backend="claude_chrome",
            )

        return ApplicationTarget(
            origin_board=self.name,
            surface="linkedin_easy_apply_assisted",
            playbook_path="playbooks/application/linkedin-easy-apply-assisted.md",
            surface_policy="automation_forbidden_on_origin",
            correlation_keys_patch={
                "origin_board": self.name,
                "origin_posting_url": posting_url,
                "posting_url": final_url,
                "redirect_chain": redirect_chain,
            },
            batch_eligible=False,
            apply_host=urlparse(final_url).netloc.lower(),
            redirect_chain=redirect_chain,
            handoff_kind="manual_assist",
            executor_backend="none",
        )

    def normalize_manual_intake(self, metadata: dict) -> dict:
        out = dict(metadata)
        out.setdefault("origin_board", self.name)
        out.setdefault("source", "linkedin_manual")
        chain = out.get("redirect_chain")
        if isinstance(chain, str):
            out["redirect_chain"] = [chain]
        elif isinstance(chain, list):
            out["redirect_chain"] = [str(item) for item in chain if str(item).strip()]
        return out

