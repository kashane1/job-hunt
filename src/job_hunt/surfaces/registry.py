from __future__ import annotations

from .base import SurfaceSpec

_SURFACE_SPECS = {
    "indeed_easy_apply": SurfaceSpec(
        surface="indeed_easy_apply",
        playbook_path="playbooks/application/indeed-easy-apply.md",
        default_executor="claude_chrome",
        default_surface_policy="browser_automated_human_submit",
        handoff_kind="automation_playbook",
    ),
    "indeed_external_redirect": SurfaceSpec(
        surface="indeed_external_redirect",
        playbook_path="playbooks/application/indeed-easy-apply.md",
        default_executor="claude_chrome",
        default_surface_policy="browser_automated_human_submit",
        handoff_kind="automation_playbook",
    ),
    "greenhouse_redirect": SurfaceSpec(
        surface="greenhouse_redirect",
        playbook_path="playbooks/application/greenhouse-redirect.md",
        default_executor="claude_chrome",
        default_surface_policy="browser_automated_human_submit",
        handoff_kind="automation_playbook",
    ),
    "lever_redirect": SurfaceSpec(
        surface="lever_redirect",
        playbook_path="playbooks/application/lever-redirect.md",
        default_executor="claude_chrome",
        default_surface_policy="browser_automated_human_submit",
        handoff_kind="automation_playbook",
    ),
    "workday_redirect": SurfaceSpec(
        surface="workday_redirect",
        playbook_path="playbooks/application/workday-redirect.md",
        default_executor="claude_chrome",
        default_surface_policy="browser_automated_human_submit",
        handoff_kind="automation_playbook",
    ),
    "ashby_redirect": SurfaceSpec(
        surface="ashby_redirect",
        playbook_path="playbooks/application/ashby-redirect.md",
        default_executor="claude_chrome",
        default_surface_policy="browser_automated_human_submit",
        handoff_kind="automation_playbook",
    ),
    "linkedin_easy_apply_assisted": SurfaceSpec(
        surface="linkedin_easy_apply_assisted",
        playbook_path="playbooks/application/linkedin-easy-apply-assisted.md",
        default_executor="none",
        default_surface_policy="automation_forbidden_on_origin",
        handoff_kind="manual_assist",
    ),
}


def get_surface_spec(surface: str) -> SurfaceSpec:
    return _SURFACE_SPECS.get(surface, _SURFACE_SPECS["indeed_easy_apply"])


def playbook_for_surface(surface: str) -> str:
    return get_surface_spec(surface).playbook_path


def surface_policy_for(surface: str) -> str:
    return get_surface_spec(surface).default_surface_policy


def executor_backend_for(surface: str) -> str:
    return get_surface_spec(surface).default_executor


def handoff_kind_for(surface: str) -> str:
    return get_surface_spec(surface).handoff_kind


def batch_eligible(surface: str, _target: object | None = None) -> bool:
    return surface not in {"indeed_external_redirect", "linkedin_easy_apply_assisted"}


def cover_letter_policy(surface: str) -> dict:
    preferred_stage = "late_documents_step"
    if surface == "workday_redirect":
        preferred_stage = "explicit_documents_step"
    elif surface == "linkedin_easy_apply_assisted":
        preferred_stage = "human_review_step"
    return {
        "should_attempt_attachment": True,
        "preferred_stage": preferred_stage,
        "text_area_policy": "manual_only",
        "required_slot_without_asset_policy": "pause_for_human_review",
    }

