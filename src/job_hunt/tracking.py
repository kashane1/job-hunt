"""Application status lifecycle management."""

from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_dir, now_iso, read_json, write_json

VALID_STAGES = [
    "not_applied",
    "applied",
    "phone_screen",
    "technical",
    "onsite",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
    "ghosted",
]

# accepted, rejected, withdrawn are fully terminal — no outbound transitions.
# ghosted is semi-terminal — companies sometimes resurface. Allows reactivation
# to interview stages but suppresses follow-ups by default.
TERMINAL_STAGES = {"accepted", "rejected", "withdrawn"}
SEMI_TERMINAL_STAGES = {"ghosted"}


def create_application_status(lead_id: str, output_dir: Path) -> dict:
    """Create a new application status record in not_applied state."""
    ensure_dir(output_dir)
    ts = now_iso()
    status = {
        "lead_id": lead_id,
        "current_stage": "not_applied",
        "transitions": [],
        "generated_content_ids": [],
        "follow_up": {
            "next_follow_up_date": "",
            "follow_up_count": 0,
            "suppress_follow_up": False,
        },
        "outcome_notes": "",
        "created_at": ts,
        "updated_at": ts,
    }
    write_json(output_dir / f"{lead_id}-status.json", status)
    return status


def update_application_status(status_path: Path, new_stage: str, note: str = "") -> dict:
    """Advance an application to a new stage.

    Validates:
    (a) new_stage is in VALID_STAGES
    (b) current stage is not in TERMINAL_STAGES
    (c) not a no-op (same stage)
    """
    if new_stage not in VALID_STAGES:
        raise ValueError(f"Invalid stage: {new_stage!r}. Valid stages: {VALID_STAGES}")

    status = read_json(status_path)
    current = status["current_stage"]

    if current in TERMINAL_STAGES:
        raise ValueError(
            f"Cannot transition from terminal stage {current!r}. "
            f"Terminal stages: {sorted(TERMINAL_STAGES)}"
        )

    if current == new_stage:
        raise ValueError(f"No-op transition: already in stage {current!r}")

    ts = now_iso()
    status["transitions"].append({
        "from_stage": current,
        "to_stage": new_stage,
        "timestamp": ts,
        "note": note,
    })
    status["current_stage"] = new_stage
    status["updated_at"] = ts

    # Terminal and semi-terminal stages suppress follow-ups.
    if new_stage in TERMINAL_STAGES | SEMI_TERMINAL_STAGES:
        status.setdefault("follow_up", {})["suppress_follow_up"] = True

    write_json(status_path, status)
    return status


def list_applications(status_dir: Path, stage_filter: str = "", since: str = "") -> list[dict]:
    """List application statuses, optionally filtering by stage and date.

    Returns a list of summary dicts suitable for JSON output.
    """
    if not status_dir.exists():
        return []

    results: list[dict] = []
    for path in sorted(status_dir.glob("*-status.json")):
        status = read_json(path)
        if stage_filter and status.get("current_stage") != stage_filter:
            continue
        if since and status.get("created_at", "") < since:
            continue

        # Find the applied_date from transitions.
        applied_date = ""
        for t in status.get("transitions", []):
            if t.get("to_stage") == "applied":
                applied_date = t["timestamp"]
                break

        results.append({
            "lead_id": status["lead_id"],
            "current_stage": status["current_stage"],
            "applied_date": applied_date,
            "updated_at": status.get("updated_at", ""),
            "path": str(path),
        })
    return results


def check_status(status_path: Path) -> dict:
    """Read and return the current status record."""
    return read_json(status_path)


def link_generated_content(status_path: Path, content_id: str) -> dict:
    """Add a generated content ID to the application status record."""
    status = read_json(status_path)
    ids = status.setdefault("generated_content_ids", [])
    if content_id not in ids:
        ids.append(content_id)
    status["updated_at"] = now_iso()
    write_json(status_path, status)
    return status


def check_integrity(data_root: Path) -> dict:
    """Single-pass set-difference scan for orphaned content and dangling references.

    Returns a JSON-serializable report with:
    - orphaned_content: content IDs not referenced by any status record
    - dangling_leads: status records whose lead_id has no matching lead file
    - dangling_companies: lead company_research_id pointing to missing company files
    - unreferenced_companies: company files not referenced by any lead
    """
    leads_dir = data_root / "leads"
    status_dir = data_root / "applications"
    content_dir = data_root / "generated"
    companies_dir = data_root / "companies"

    # Build ID sets.
    lead_ids: set[str] = set()
    if leads_dir.exists():
        for p in leads_dir.glob("*.json"):
            try:
                lead = read_json(p)
                lead_ids.add(lead.get("lead_id", p.stem))
            except (json.JSONDecodeError, KeyError):
                continue

    status_lead_ids: set[str] = set()
    referenced_content_ids: set[str] = set()
    if status_dir.exists():
        for p in status_dir.glob("*-status.json"):
            try:
                status = read_json(p)
                status_lead_ids.add(status["lead_id"])
                for cid in status.get("generated_content_ids", []):
                    referenced_content_ids.add(cid)
            except (json.JSONDecodeError, KeyError):
                continue

    content_ids: set[str] = set()
    for subdir_name in ("resumes", "cover-letters", "answers", "follow-ups"):
        subdir = content_dir / subdir_name
        if subdir.exists():
            for p in subdir.glob("*.json"):
                try:
                    content = read_json(p)
                    content_ids.add(content.get("content_id", p.stem))
                except (json.JSONDecodeError, KeyError):
                    continue

    company_ids: set[str] = set()
    if companies_dir.exists():
        for p in companies_dir.glob("*.json"):
            try:
                company = read_json(p)
                company_ids.add(company.get("company_id", p.stem))
            except (json.JSONDecodeError, KeyError):
                continue

    lead_company_refs: set[str] = set()
    if leads_dir.exists():
        for p in leads_dir.glob("*.json"):
            try:
                lead = read_json(p)
                crid = lead.get("company_research_id")
                if crid:
                    lead_company_refs.add(crid)
            except (json.JSONDecodeError, KeyError):
                continue

    return {
        "orphaned_content": sorted(content_ids - referenced_content_ids),
        "dangling_leads": sorted(status_lead_ids - lead_ids),
        "dangling_companies": sorted(lead_company_refs - company_ids),
        "unreferenced_companies": sorted(company_ids - lead_company_refs),
    }
