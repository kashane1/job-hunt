"""packet_review: safe, read-only review of generated application packets.

Reads ONLY JSON metadata from ``data/applications/<draft>/`` (plan.json,
status.json) plus the linked ``data/generated/*`` content descriptors and the
lead JSON. It NEVER opens the private resume / cover-letter markdown prose or the
claims-bank prose — it derives claim counts and an approval boolean from
claims-bank metadata only. The module performs no apply / submit / browser /
form actions; it strictly reports so a human can decide what to inspect next.

Safety invariant: a packet whose ``requires_human_submit`` is not exactly True is
flagged as a hard ``safety_error`` (recommended action ``hold_safety``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .application import MANUAL_CLOSED_STATUSES
from .utils import repo_root

# Fix hints for known PDF-export error codes, used when a record predates the
# persisted remediation field. Tooling text only — never private content.
_KNOWN_REMEDIATION = {
    "weasyprint_missing": "pip install 'job-hunt[pdf]'  (or: pip install weasyprint)",
}

# Lifecycle states that mean the packet is finished / out of the review queue.
_TERMINAL_STATES = frozenset({
    "submitted",
    "applied",
    "applied_externally",
    "withdrawn",
    "closed",
    "rejected",
})


def _read_json_safe(path: Path) -> dict:
    """Read a JSON object, returning {} on any error (best-effort, never raises)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_claims_index(claims_path: Path | None) -> dict[str, bool]:
    """Map claim_id -> is_approved from the claims bank.

    Only the approval boolean is retained — no claim text ever leaves this
    function. Returns {} when the file is absent/unreadable (claims approval then
    reports as unknown rather than failing the review).
    """
    if claims_path is None or not claims_path.exists():
        return {}
    data = _read_json_safe(claims_path)
    claims = data.get("claims") if isinstance(data, dict) else None
    out: dict[str, bool] = {}
    if isinstance(claims, list):
        for c in claims:
            if isinstance(c, dict) and c.get("claim_id"):
                out[str(c["claim_id"])] = c.get("review_status") == "approved"
    return out


def _freshness(lead: dict, now: datetime) -> dict | None:
    """Best-effort posting freshness from the lead's discovery metadata.

    Prefers the most recent ``listing_updated_at`` (the posting's own update
    time); falls back to ``ingested_at``. Returns basis/timestamp/age_hours, or
    None when no usable timestamp exists.
    """
    candidates: list[tuple[str, datetime]] = []
    for src in lead.get("discovered_via") or []:
        if isinstance(src, dict):
            dt = _parse_dt(src.get("listing_updated_at"))
            if dt:
                candidates.append(("posted_at", dt))
    if candidates:
        basis, dt = max(candidates, key=lambda t: t[1])
    else:
        dt = _parse_dt(lead.get("ingested_at"))
        if not dt:
            return None
        basis = "ingested_at"
    age_hours = round((now - dt).total_seconds() / 3600.0, 1)
    return {"basis": basis, "timestamp": dt.isoformat(), "age_hours": age_hours}


def _claim_ids(*descriptors: dict) -> set[str]:
    """Collect ``claim:`` source-document ids from generated content descriptors."""
    ids: set[str] = set()
    for d in descriptors:
        for ref in d.get("source_document_ids") or []:
            if isinstance(ref, str) and ref.startswith("claim:"):
                ids.add(ref[len("claim:"):])
    return ids


def _warning_codes(descriptor: dict) -> list[str]:
    out: list[str] = []
    for w in descriptor.get("generation_warnings") or []:
        if isinstance(w, dict) and w.get("code"):
            out.append(str(w["code"]))
        elif isinstance(w, str):
            out.append(w)
    return out


def _pdf_asset_status(ref: dict, descriptor: dict) -> str:
    """PDF status for a single asset: ready / failed / not_attempted / unavailable.

    Prefers ground truth from the generated record (a real ``pdf_path`` means
    ready; a recorded ``pdf_export_error_code`` means failed), then falls back to
    whatever packet-prepare stamped into the plan's asset ref.
    """
    if descriptor.get("pdf_path"):
        return "ready"
    if descriptor.get("pdf_export_error_code"):
        return "failed"
    return str(ref.get("pdf_export_status") or "not_attempted")


def _pdf_export(refs: dict, resume: dict, cover: dict) -> dict:
    """Combined PDF-export view for a packet (statuses + tooling diagnostics).

    Diagnostics here are tooling-level only (error code + remediation command) —
    never resume/cover-letter prose.
    """
    statuses = {
        "resume": _pdf_asset_status(refs.get("resume") or {}, resume),
        "cover_letter": _pdf_asset_status(refs.get("cover_letter") or {}, cover),
    }
    present = list(statuses.values())
    if any(s == "failed" for s in present):
        overall = "failed"
    elif present and all(s == "ready" for s in present):
        overall = "ready"
    elif any(s == "unavailable" for s in present):
        overall = "unavailable"
    else:
        overall = "not_attempted"
    error_code = resume.get("pdf_export_error_code") or cover.get("pdf_export_error_code") or None
    remediation = resume.get("pdf_export_remediation") or cover.get("pdf_export_remediation") or None
    if remediation is None and error_code:
        # Records written before remediation was persisted still get a fix hint.
        remediation = _KNOWN_REMEDIATION.get(error_code)
    return {
        "overall": overall,
        "resume": statuses["resume"],
        "cover_letter": statuses["cover_letter"],
        "error_code": error_code,
        "remediation": remediation,
    }


def _find_generated(generated_dir: Path, content_id: str) -> dict:
    """Locate a generated content descriptor JSON by content_id under any subdir."""
    if not content_id:
        return {}
    for sub in ("cover-letters", "resumes", "ats-checks"):
        p = generated_dir / sub / f"{content_id}.json"
        if p.exists():
            return _read_json_safe(p)
    return {}


def assess_packet(
    draft_dir: Path,
    *,
    data_root: Path,
    claims_index: dict[str, bool],
    now: datetime,
    dup_map: dict[str, list[str]],
) -> dict | None:
    """Assemble a safe, prose-free review record for a single packet directory."""
    plan = _read_json_safe(draft_dir / "plan.json")
    status = _read_json_safe(draft_dir / "status.json")
    if not plan and not status:
        return None

    draft_id = plan.get("draft_id") or status.get("draft_id") or draft_dir.name
    lead_id = plan.get("lead_id") or status.get("lead_id") or ""

    lead = _read_json_safe(data_root / "leads" / f"{lead_id}.json") if lead_id else {}
    fit = lead.get("fit_assessment") or {}

    generated_dir = data_root / "generated"
    content_ids = status.get("generated_content_ids") or []
    cover = resume = {}
    for cid in content_ids:
        if "cover-letter" in str(cid):
            cover = _find_generated(generated_dir, str(cid))
        else:
            resume = _find_generated(generated_dir, str(cid))

    # Lane: prefer the resume variant style, then the cover-letter lane.
    lane = (
        resume.get("variant_style")
        or cover.get("variant_style")
        or cover.get("lane_id")
        or None
    )

    # ATS counts from plan.json's embedded check (self-contained, already parsed).
    ats = plan.get("ats_check") or {}
    ats_errors = len(ats.get("errors") or [])
    ats_warnings = len(ats.get("warnings") or [])

    coherence = len(plan.get("coherence_warnings") or [])

    # Artifact availability from plan's generated_asset_refs (no prose read).
    refs = plan.get("generated_asset_refs") or {}
    resume_ref = refs.get("resume") or {}
    cover_ref = refs.get("cover_letter") or {}
    artifacts = {
        "plan": (draft_dir / "plan.json").exists(),
        "status": (draft_dir / "status.json").exists(),
        "resume": bool(resume_ref.get("available")) or bool(resume),
        "cover_letter": bool(cover_ref.get("available")) or bool(cover),
    }
    artifacts_present = all(artifacts.values())

    # Claims: count + approval, derived only from metadata booleans.
    claim_ids = _claim_ids(resume, cover)
    claims_total = len(claim_ids)
    if claims_index:
        approved = sum(1 for cid in claim_ids if claims_index.get(cid))
        unknown = sum(1 for cid in claim_ids if cid not in claims_index)
        all_approved = claims_total > 0 and approved == claims_total
    else:
        approved = 0
        unknown = claims_total
        all_approved = False
    claims = {
        "total": claims_total,
        "approved": approved,
        "unknown": unknown,
        "all_approved": all_approved,
    }

    pdf = _pdf_export(refs, resume, cover)

    safety_warnings = sorted(set(_warning_codes(cover) + _warning_codes(resume)))

    requires_human_submit = status.get("requires_human_submit")
    safety_error = requires_human_submit is not True

    lifecycle_state = status.get("lifecycle_state") or "unknown"
    current_stage = status.get("current_stage") or "unknown"

    duplicate_of = [d for d in dup_map.get(lead_id, []) if d != draft_id]

    # Human disposition recorded via mark-packet (separate from the browser
    # lifecycle machine). Metadata only — never the human's prose is required.
    manual = status.get("manual_disposition") or {}
    manual_status = manual.get("status")
    manual_closed = manual_status in MANUAL_CLOSED_STATUSES

    # Attention reasons = genuine problems a human must resolve.
    attention: list[str] = []
    if safety_error:
        attention.append("missing_human_submit_flag")
    if ats_errors:
        attention.append("ats_errors")
    if coherence:
        attention.append("coherence_warnings")
    if claims_total == 0:
        attention.append("no_claim_sources")
    elif not all_approved:
        attention.append("claims_not_all_approved")
    if not artifacts_present:
        attention.append("missing_artifacts")
    if pdf["overall"] == "failed":
        attention.append("pdf_export_failed")
    if manual_status == "needs_revision":
        attention.append("marked_needs_revision")
    # A packet the human has already submitted/skipped/closed is out of the
    # action queue: drop quality reasons (still surface a real safety gap).
    if manual_closed:
        attention = [a for a in attention if a == "missing_human_submit_flag"]
    needs_attention = bool(attention)

    # Soft notes worth a glance but not blockers.
    notes: list[str] = []
    if safety_warnings:
        notes.append("claim_safety_filtered")
    if ats_warnings:
        notes.append("ats_warnings")
    if duplicate_of:
        notes.append("duplicate_existing_packet")
    if pdf["overall"] not in ("ready", "failed") and not manual_closed:
        # Not failed, but no PDF yet either — don't let it look cleanly ready.
        notes.append("pdf_not_ready")
    if manual_status == "follow_up_later":
        notes.append("parked_follow_up_later")

    # A packet is "ready for review" only if it is clean AND the human has not
    # already closed it (submitted/skipped/parked/needs-revision).
    ready_for_review = (
        not safety_error
        and lifecycle_state not in _TERMINAL_STATES
        and not needs_attention
        and not duplicate_of
        and not manual_closed
        and manual_status not in ("needs_revision", "follow_up_later")
    )

    checklist_present = (draft_dir / "MANUAL_SUBMISSION.md").exists()

    record = {
        "draft_id": draft_id,
        "lead_id": lead_id,
        "manual_submission_present": checklist_present,
        "company": lead.get("company") or "",
        "title": lead.get("title") or "",
        "lane": lane,
        "tier": plan.get("tier") or status.get("tier") or None,
        "score": fit.get("fit_score"),
        "score_label": fit.get("fit_recommendation"),
        "freshness": _freshness(lead, now),
        "lifecycle_state": lifecycle_state,
        "current_stage": current_stage,
        "requires_human_submit": requires_human_submit is True,
        "artifacts": artifacts,
        "artifacts_present": artifacts_present,
        "ats": {"errors": ats_errors, "warnings": ats_warnings, "status": ats.get("status")},
        "pdf": pdf,
        "coherence_warnings": coherence,
        "claims": claims,
        "claim_safety_warnings": safety_warnings,
        "duplicate_of": duplicate_of,
        "manual": {
            "status": manual_status,
            "updated_at": manual.get("updated_at"),
            "next_follow_up_date": manual.get("next_follow_up_date"),
        } if manual_status else None,
        "safety_error": safety_error,
        "needs_attention": needs_attention,
        "attention_reasons": attention,
        "notes": notes,
        "ready_for_review": ready_for_review,
    }
    record["recommended_action"] = recommend_action(record)
    return record


def recommend_action(packet: dict) -> str:
    """Map a packet record to a single safe human action.

    Never returns an auto-submit action — 'manual_submit' means the human's next
    concrete step is to review and submit by hand. The tool submits nothing.
    """
    if packet["safety_error"]:
        return "hold_safety"
    # Human disposition (mark-packet) takes precedence over the auto-derived
    # action: a packet the person has already acted on should not re-surface.
    manual_status = (packet.get("manual") or {}).get("status")
    if manual_status in ("manually_submitted", "interviewing"):
        return "track"
    if manual_status in ("skipped", "not_interested", "rejected"):
        return "skip"
    if manual_status == "needs_revision":
        return "revise"
    if manual_status == "follow_up_later":
        return "follow_up"
    if manual_status == "reviewed":
        # Human-reviewed: revise if a blocker remains, else it is theirs to submit.
        return "revise" if packet["needs_attention"] else "manual_submit"
    if packet["lifecycle_state"] in _TERMINAL_STATES:
        return "skip"
    if packet["duplicate_of"]:
        return "skip"
    if packet["needs_attention"]:
        return "revise"
    if packet["notes"]:
        # Clean enough to submit, but has soft notes worth a human glance first.
        return "review"
    return "manual_submit"


def _build_dup_map(draft_dirs: Iterable[Path]) -> dict[str, list[str]]:
    dup: dict[str, list[str]] = {}
    for d in draft_dirs:
        status = _read_json_safe(d / "status.json")
        plan = _read_json_safe(d / "plan.json")
        lead_id = plan.get("lead_id") or status.get("lead_id")
        draft_id = plan.get("draft_id") or status.get("draft_id") or d.name
        if lead_id:
            dup.setdefault(str(lead_id), []).append(str(draft_id))
    return dup


def review_packets(
    *,
    data_root: Path | None = None,
    claims_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Review every packet under ``data_root/applications`` (read-only)."""
    data_root = data_root or (repo_root() / "data")
    now = now or datetime.now(timezone.utc)
    apps_root = data_root / "applications"
    if not apps_root.is_dir():
        return []
    draft_dirs = [
        d for d in sorted(apps_root.iterdir())
        if d.is_dir() and d.name not in ("batches", "_suspicious", "manual-drafts")
        and (d / "status.json").exists()
    ]
    claims_index = load_claims_index(claims_path)
    dup_map = _build_dup_map(draft_dirs)
    out: list[dict] = []
    for d in draft_dirs:
        rec = assess_packet(
            d, data_root=data_root, claims_index=claims_index, now=now, dup_map=dup_map,
        )
        if rec is not None:
            out.append(rec)
    # Stable, useful order: needs-attention first, then by score desc, then fresh.
    def _sort_key(r: dict):
        score = r.get("score")
        score = score if isinstance(score, (int, float)) else -1
        age = (r.get("freshness") or {}).get("age_hours")
        age = age if isinstance(age, (int, float)) else 1e9
        return (0 if r["needs_attention"] else 1, -score, age)
    out.sort(key=_sort_key)
    return out


def packet_history(
    draft_id: str,
    *,
    data_root: Path | None = None,
    claims_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Read-only safe history for a single packet (no prose).

    Returns the current manual + browser-lifecycle status, both timelines, packet
    readiness, and whether human submit is required. Reads only JSON metadata
    (status.json, plan.json, generated descriptors, lead) — never resume /
    cover-letter prose. ``found`` is False for an unknown draft id (safe, no raise).
    """
    data_root = data_root or (repo_root() / "data")
    now = now or datetime.now(timezone.utc)
    draft_dir = data_root / "applications" / draft_id
    if not (draft_dir / "status.json").exists():
        return {"found": False, "draft_id": draft_id}
    claims_index = load_claims_index(claims_path)
    rec = assess_packet(
        draft_dir, data_root=data_root, claims_index=claims_index, now=now, dup_map={},
    )
    if rec is None:
        return {"found": False, "draft_id": draft_id}

    status = _read_json_safe(draft_dir / "status.json")
    disp = status.get("manual_disposition") or {}

    manual_timeline = [
        {
            "status": e.get("status"),
            "at": e.get("at"),
            "note": e.get("note", ""),
            "submitted_url": e.get("submitted_url", ""),
            "portal_url": e.get("portal_url", ""),
            "next_follow_up_date": e.get("next_follow_up_date", ""),
        }
        for e in (disp.get("history") or []) if isinstance(e, dict)
    ]
    # Browser-lifecycle transitions + events are metadata only (no prose).
    lifecycle_timeline = [
        {
            "from_stage": t.get("from_stage"),
            "to_stage": t.get("to_stage"),
            "timestamp": t.get("timestamp"),
            "note": t.get("note", ""),
        }
        for t in (status.get("transitions") or []) if isinstance(t, dict)
    ]
    events = [
        {"type": ev.get("type"), "occurred_at": ev.get("occurred_at")}
        for ev in (status.get("events") or []) if isinstance(ev, dict)
    ]

    return {
        "found": True,
        "draft_id": rec["draft_id"],
        "lead_id": rec["lead_id"],
        "company": rec["company"],
        "title": rec["title"],
        "lane": rec["lane"],
        "manual_status": (rec.get("manual") or {}).get("status"),
        "lifecycle_state": rec["lifecycle_state"],
        "current_stage": rec["current_stage"],
        "requires_human_submit": rec["requires_human_submit"],
        "readiness": {
            "ready_for_review": rec["ready_for_review"],
            "needs_attention": rec["needs_attention"],
            "attention_reasons": rec["attention_reasons"],
            "pdf": (rec.get("pdf") or {}).get("overall"),
            "artifacts_present": rec["artifacts_present"],
            "manual_submission_present": rec.get("manual_submission_present", False),
        },
        "recommended_action": rec["recommended_action"],
        "manual_timeline": manual_timeline,
        "lifecycle_timeline": lifecycle_timeline,
        "events": events,
        "follow_up": {
            "next_follow_up_date": disp.get("next_follow_up_date"),
            "submitted_url": disp.get("submitted_url"),
            "portal_url": disp.get("portal_url"),
        },
    }


def apply_filters(
    reviews: list[dict],
    *,
    lane: str | None = None,
    company: str | None = None,
    ready_only: bool = False,
    needs_attention: bool = False,
    limit: int | None = None,
) -> list[dict]:
    rows = reviews
    if lane:
        rows = [r for r in rows if (r.get("lane") or "").casefold() == lane.casefold()]
    if company:
        rows = [r for r in rows if company.casefold() in (r.get("company") or "").casefold()]
    if ready_only:
        rows = [r for r in rows if r["ready_for_review"]]
    if needs_attention:
        rows = [r for r in rows if r["needs_attention"]]
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows


def summarize(reviews: list[dict]) -> dict:
    """Aggregate counts over a (possibly filtered) review list."""
    actions: dict[str, int] = {}
    for r in reviews:
        actions[r["recommended_action"]] = actions.get(r["recommended_action"], 0) + 1
    return {
        "total": len(reviews),
        "ready_for_review": sum(1 for r in reviews if r["ready_for_review"]),
        "needs_attention": sum(1 for r in reviews if r["needs_attention"]),
        "safety_errors": sum(1 for r in reviews if r["safety_error"]),
        "by_action": actions,
    }
