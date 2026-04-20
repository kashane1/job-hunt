from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import ApplicationTarget

_URL_RE = re.compile(r"^https?://(?:www\.|secure\.)?indeed\.com/", re.IGNORECASE)


class IndeedBoardAdapter:
    name = "indeed"

    def matches_lead_or_url(self, lead: dict | None, url: str) -> bool:
        source = str((lead or {}).get("source", ""))
        board = str((lead or {}).get("origin_board", ""))
        return bool(_URL_RE.search(url) or source.startswith("indeed") or board == self.name)

    def resolve_application_target(
        self,
        lead: dict,
        *,
        posting_url: str,
        apply_type: str | None = None,
    ) -> ApplicationTarget:
        surface = "indeed_external_redirect" if apply_type == "external" else "indeed_easy_apply"
        host = urlparse(posting_url).netloc.lower()
        return ApplicationTarget(
            origin_board=self.name,
            surface=surface,
            correlation_keys_patch={
                "origin_board": self.name,
                "origin_posting_url": posting_url,
            },
            apply_host=host,
            redirect_chain=[],
        )

    def normalize_manual_intake(self, metadata: dict) -> dict:
        out = dict(metadata)
        out.setdefault("origin_board", self.name)
        return out
