from __future__ import annotations

from dataclasses import replace

from .base import ApplicationTarget, BoardAdapter
from .glassdoor import GlassdoorBoardAdapter
from .indeed import IndeedBoardAdapter
from .linkedin import LinkedInBoardAdapter
from .routing import surface_for_external_url
from ..surfaces.registry import (
    batch_eligible as surface_batch_eligible,
    executor_backend_for,
    handoff_kind_for,
    playbook_for_surface as surface_playbook_for_surface,
    surface_policy_for,
)

_ADAPTERS: tuple[BoardAdapter, ...] = (
    LinkedInBoardAdapter(),
    GlassdoorBoardAdapter(),
    IndeedBoardAdapter(),
)


def _hydrate_surface_metadata(target: ApplicationTarget) -> ApplicationTarget:
    return replace(
        target,
        playbook_path=surface_playbook_for_surface(target.surface),
        surface_policy=surface_policy_for(target.surface),
        batch_eligible=surface_batch_eligible(target.surface, target),
        handoff_kind=handoff_kind_for(target.surface),
        executor_backend=executor_backend_for(target.surface),
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
        surface = surface_for_external_url(posting_url)
        if surface is not None:
            return _hydrate_surface_metadata(ApplicationTarget(
                origin_board=str((lead or {}).get("origin_board") or "unknown"),
                surface=surface,
                correlation_keys_patch={},
                handoff_kind="automation_playbook",
            ))
    return _hydrate_surface_metadata(
        adapter.resolve_application_target(lead or {}, posting_url=posting_url, apply_type=apply_type)
    )


def playbook_for_surface(surface: str) -> str:
    return surface_playbook_for_surface(surface)
