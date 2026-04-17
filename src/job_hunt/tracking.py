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
    """Single-pass set-difference scan plus file-level checks for orphaned artifacts.

    Returns a JSON-serializable report with:
    - orphaned_content: content IDs not referenced by any status record
    - dangling_leads: status records whose lead_id has no matching lead file
    - dangling_companies: lead company_research_id pointing to missing company files
    - unreferenced_companies: company files not referenced by any lead

    Batch 2 additions (file-level pointer checks):
    - missing_source_files: content records whose output_path doesn't exist
    - orphaned_pdfs: content records whose pdf_path doesn't exist on disk
    - orphaned_ats_reports: content records whose ats_check.report_path doesn't exist
    - stuck_pending_ats: content records with ats_check.status == "pending"
    - check_failed_ats: content records with ats_check.status == "check_failed"
    - stale_pdfs: content records where pdf_generated_at < generated_at
    - stale_ats_checks: content records where ats_check.checked_at < generated_at
    - stale_intake_pending: intake files in _intake/pending/ older than 1 hour
    - stale_intake_failed: intake files in _intake/failed/ older than 7 days
    """
    from datetime import datetime, timedelta, timezone
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

    # Batch 2: file-level pointer checks on content records
    missing_source_files: list[dict] = []
    orphaned_pdfs: list[dict] = []
    orphaned_ats_reports: list[dict] = []
    stuck_pending_ats: list[dict] = []
    check_failed_ats: list[dict] = []
    stale_pdfs: list[dict] = []
    stale_ats_checks: list[dict] = []

    for subdir_name in ("resumes", "cover-letters", "answers", "follow-ups"):
        subdir = content_dir / subdir_name
        if not subdir.exists():
            continue
        for p in subdir.glob("*.json"):
            try:
                content = read_json(p)
            except (json.JSONDecodeError, KeyError):
                continue
            cid = content.get("content_id", p.stem)
            generated_at = content.get("generated_at", "")

            output_path = content.get("output_path")
            if output_path and not Path(output_path).exists():
                missing_source_files.append({"content_id": cid, "missing_path": output_path})

            pdf_path = content.get("pdf_path")
            if pdf_path:
                if not Path(pdf_path).exists():
                    orphaned_pdfs.append({"content_id": cid, "pdf_path": pdf_path})
                else:
                    pdf_at = content.get("pdf_generated_at", "")
                    if pdf_at and generated_at and pdf_at < generated_at:
                        stale_pdfs.append({
                            "content_id": cid,
                            "generated_at": generated_at,
                            "pdf_generated_at": pdf_at,
                        })

            ats_check = content.get("ats_check") or {}
            status_value = ats_check.get("status")
            if status_value == "pending":
                stuck_pending_ats.append({
                    "content_id": cid,
                    "stuck_since": ats_check.get("checked_at", ""),
                })
            elif status_value == "check_failed":
                check_failed_ats.append({
                    "content_id": cid,
                    "error": ats_check.get("error", ""),
                })
            report_path = ats_check.get("report_path")
            if report_path and not Path(report_path).exists():
                orphaned_ats_reports.append({"content_id": cid, "report_path": report_path})
            checked_at = ats_check.get("checked_at", "")
            if checked_at and generated_at and checked_at < generated_at:
                stale_ats_checks.append({
                    "content_id": cid,
                    "generated_at": generated_at,
                    "ats_checked_at": checked_at,
                })

    # Batch 2: intake directory staleness checks
    stale_intake_pending: list[dict] = []
    stale_intake_failed: list[dict] = []
    now = datetime.now(timezone.utc)
    intake_root = leads_dir / "_intake"
    for subname, threshold, bucket in (
        ("pending", timedelta(hours=1), stale_intake_pending),
        ("failed", timedelta(days=7), stale_intake_failed),
    ):
        subdir = intake_root / subname
        if not subdir.exists():
            continue
        for p in subdir.glob("*.md"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            age = now - mtime
            if age > threshold:
                bucket.append({"path": str(p), "age_seconds": int(age.total_seconds())})

    # Batch 3: discovery orphan checks
    stale_review_entries: list[dict] = []
    unscored_discovered_leads: list[dict] = []
    stale_tmp_files: list[dict] = []

    discovery_root = data_root / "discovery"
    review_dir = discovery_root / "review"
    if review_dir.exists():
        for p in review_dir.glob("*.md"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            age = now - mtime
            if age > timedelta(days=30):
                stale_review_entries.append({
                    "path": str(p),
                    "age_seconds": int(age.total_seconds()),
                })

    if leads_dir.exists():
        for p in leads_dir.glob("*.json"):
            try:
                lead = read_json(p)
            except (json.JSONDecodeError, KeyError):
                continue
            if lead.get("status") != "discovered" or lead.get("fit_assessment"):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            age = now - mtime
            if age > timedelta(hours=1):
                unscored_discovered_leads.append({
                    "lead_id": lead.get("lead_id", p.stem),
                    "path": str(p),
                    "age_seconds": int(age.total_seconds()),
                })

    if data_root.exists():
        for p in data_root.rglob("*.tmp"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            age = now - mtime
            if age > timedelta(hours=1):
                stale_tmp_files.append({
                    "path": str(p),
                    "age_seconds": int(age.total_seconds()),
                })

    report = {
        "orphaned_content": sorted(content_ids - referenced_content_ids),
        "dangling_leads": sorted(status_lead_ids - lead_ids),
        "dangling_companies": sorted(lead_company_refs - company_ids),
        "unreferenced_companies": sorted(company_ids - lead_company_refs),
        "missing_source_files": missing_source_files,
        "orphaned_pdfs": orphaned_pdfs,
        "orphaned_ats_reports": orphaned_ats_reports,
        "stuck_pending_ats": stuck_pending_ats,
        "check_failed_ats": check_failed_ats,
        "stale_pdfs": stale_pdfs,
        "stale_ats_checks": stale_ats_checks,
        "stale_intake_pending": stale_intake_pending,
        "stale_intake_failed": stale_intake_failed,
        "stale_review_entries": stale_review_entries,
        "unscored_discovered_leads": unscored_discovered_leads,
        "stale_tmp_files": stale_tmp_files,
    }
    has_issues = any(
        bool(v) for k, v in report.items() if k != "unreferenced_companies"
    )
    report["summary"] = {
        "has_issues": has_issues,
        "issue_counts": {k: len(v) for k, v in report.items() if isinstance(v, list) and k != "unreferenced_companies"},
    }
    return report
