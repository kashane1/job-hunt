"""Follow-up reminder scheduling and draft generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .utils import ensure_dir, now_iso, read_json, slugify

DEFAULT_FOLLOW_UP_SCHEDULE = [
    {"days_after_apply": 10, "type": "check_in"},
    {"days_after_apply": 24, "type": "follow_up"},
]


def check_follow_ups(status_dir: Path) -> list[dict]:
    """Scan all application status files and report which are due for follow-up.

    Returns a JSON-serializable list sorted by urgency (most overdue first).
    """
    if not status_dir.exists():
        return []

    now = datetime.now(UTC)
    results: list[dict] = []

    for path in sorted(status_dir.glob("*-status.json")):
        try:
            status = read_json(path)
        except Exception:
            continue

        lead_id = status.get("lead_id", "")
        current_stage = status.get("current_stage", "")
        follow_up = status.get("follow_up", {})
        suppressed = follow_up.get("suppress_follow_up", False)
        follow_up_count = follow_up.get("follow_up_count", 0)

        # Find applied date.
        applied_date = ""
        for t in status.get("transitions", []):
            if t.get("to_stage") == "applied":
                applied_date = t["timestamp"]
                break

        if not applied_date:
            continue

        try:
            applied_dt = datetime.fromisoformat(applied_date)
        except ValueError:
            continue

        days_since = (now - applied_dt).days

        # Determine which follow-up is due (if any).
        for schedule_item in DEFAULT_FOLLOW_UP_SCHEDULE:
            if days_since >= schedule_item["days_after_apply"] and follow_up_count < (
                DEFAULT_FOLLOW_UP_SCHEDULE.index(schedule_item) + 1
            ):
                suppress_reason = ""
                if suppressed:
                    suppress_reason = f"Stage is {current_stage}"

                results.append({
                    "lead_id": lead_id,
                    "current_stage": current_stage,
                    "applied_date": applied_date,
                    "days_since": days_since,
                    "follow_up_type": schedule_item["type"],
                    "suppressed": suppressed,
                    "suppress_reason": suppress_reason,
                    "path": str(path),
                })
                break

    results.sort(key=lambda x: x["days_since"], reverse=True)
    return results


def generate_follow_up_draft(
    lead_id: str,
    candidate_name: str,
    company_name: str,
    job_title: str,
    matched_skills: list[str],
    follow_up_type: str,
    output_dir: Path,
) -> dict:
    """Generate a follow-up email draft as markdown.

    Accepts narrowed input — NOT the full candidate_profile dict.
    This prevents PII (email, phone, salary) from leaking into follow-up drafts.
    """
    ensure_dir(output_dir)
    ts = now_iso()
    lead_slug = slugify(f"{company_name}-{job_title}")
    ts_compact = ts.replace(":", "").replace("-", "").replace("+", "").replace("T", "T")[:15] or ts
    filename = f"{lead_slug}-{follow_up_type}-{ts_compact}.md"

    skills_text = ", ".join(matched_skills[:4]) if matched_skills else "my relevant experience"

    if follow_up_type == "check_in":
        body = f"""Subject: Following up — {job_title} application

Dear Hiring Manager,

I recently submitted my application for the {job_title} position at {company_name} and wanted to follow up to express my continued interest.

My background in {skills_text} aligns well with the requirements of this role, and I would welcome the opportunity to discuss how I can contribute to your team.

Thank you for your time and consideration.

Best regards,
{candidate_name}
"""
    else:
        body = f"""Subject: Continued interest — {job_title} at {company_name}

Dear Hiring Manager,

I am writing to reiterate my interest in the {job_title} role at {company_name}. Since my initial application, I remain enthusiastic about the opportunity to contribute my expertise in {skills_text}.

I would appreciate any update on the status of my application and remain available for a conversation at your convenience.

Thank you for your consideration.

Best regards,
{candidate_name}
"""

    path = output_dir / filename
    path.write_text(body, encoding="utf-8")
    return {"path": str(path), "lead_id": lead_id, "follow_up_type": follow_up_type}


def suppress_follow_up(status_path: Path, reason: str) -> dict:
    """Suppress follow-ups for an application."""
    status = read_json(status_path)
    status.setdefault("follow_up", {})["suppress_follow_up"] = True
    status["outcome_notes"] = (status.get("outcome_notes", "") + f"\nFollow-up suppressed: {reason}").strip()
    from .utils import write_json
    write_json(status_path, status)
    return status
