from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import ApplicationTarget
from .routing import final_reroute_target, normalize_redirect_chain

_GLASSDOOR_URL_RE = re.compile(r"^https?://(?:[\w-]+\.)?glassdoor\.com/", re.IGNORECASE)


class GlassdoorBoardAdapter:
    name = "glassdoor"

    def matches_lead_or_url(self, lead: dict | None, url: str) -> bool:
        source = str((lead or {}).get("source", ""))
        board = str((lead or {}).get("origin_board", ""))
        redirect_chain = (lead or {}).get("redirect_chain") or []
        return bool(
            _GLASSDOOR_URL_RE.search(url)
            or source.startswith("glassdoor")
            or board == self.name
            or any(_GLASSDOOR_URL_RE.search(str(item)) for item in redirect_chain)
        )

    def resolve_application_target(
        self,
        lead: dict,
        *,
        posting_url: str,
        apply_type: str | None = None,
    ) -> ApplicationTarget:
        del apply_type
        origin_posting_url = str(lead.get("posting_url") or posting_url)
        redirect_chain = normalize_redirect_chain(lead.get("redirect_chain"))
        final_url = str(
            lead.get("canonical_url")
            or lead.get("application_url")
            or lead.get("posting_url")
            or posting_url
        )
        ats_surface, final_url = final_reroute_target(final_url, redirect_chain)
        correlation_keys_patch = {
            "origin_board": self.name,
            "origin_posting_url": origin_posting_url,
            "posting_url": final_url,
            "redirect_chain": redirect_chain,
        }

        if ats_surface is not None:
            return ApplicationTarget(
                origin_board=self.name,
                surface=ats_surface,
                correlation_keys_patch=correlation_keys_patch,
                apply_host=urlparse(final_url).netloc.lower(),
                redirect_chain=redirect_chain,
            )

        return ApplicationTarget(
            origin_board=self.name,
            surface="glassdoor_easy_apply",
            correlation_keys_patch=correlation_keys_patch,
            apply_host=urlparse(final_url).netloc.lower(),
            redirect_chain=redirect_chain,
        )

    def normalize_manual_intake(self, metadata: dict) -> dict:
        out = dict(metadata)
        out.setdefault("origin_board", self.name)
        out.setdefault("source", "glassdoor_manual")
        out["redirect_chain"] = normalize_redirect_chain(out.get("redirect_chain"))
        if not out["redirect_chain"]:
            out.pop("redirect_chain", None)
        return out
