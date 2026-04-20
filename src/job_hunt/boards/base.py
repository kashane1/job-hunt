from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ApplicationTarget:
    origin_board: str
    surface: str
    playbook_path: str = ""
    surface_policy: str = ""
    correlation_keys_patch: dict[str, object] = field(default_factory=dict)
    batch_eligible: bool = True
    apply_host: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    handoff_kind: str = "automation_playbook"
    executor_backend: str = "claude_chrome"


class BoardAdapter(Protocol):
    name: str

    def matches_lead_or_url(self, lead: dict | None, url: str) -> bool: ...

    def resolve_application_target(
        self,
        lead: dict,
        *,
        posting_url: str,
        apply_type: str | None = None,
    ) -> ApplicationTarget: ...

    def normalize_manual_intake(self, metadata: dict) -> dict: ...


class RemoteIngestionAdapter(BoardAdapter, Protocol):
    def ingest_remote_metadata(
        self,
        url: str,
        html_text: str | None = None,
    ) -> dict: ...
