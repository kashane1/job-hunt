from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SurfaceSpec:
    surface: str
    playbook_path: str
    default_executor: str
    default_surface_policy: str
    handoff_kind: str
    humanize_eligible: bool = False

