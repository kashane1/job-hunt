"""packet_checklist: generate a safe ``MANUAL_SUBMISSION.md`` inside each packet.

Every prepared application packet gets a single human-facing checklist file that
gathers exactly what a person needs to finish the application *by hand*: the
company / title / posting URL, the resume + cover-letter file paths (PDF first,
markdown fallback), the safe review metadata (ATS counts, claims approval count,
claim-safety filter flags), an explicit manual review checklist, the exact
``mark-packet`` post-action commands, and a human-submit safety notice.

Safety invariants (mirrors ``packet_review``):
  * Reads ONLY JSON metadata (status.json, plan.json, generated descriptors, the
    lead JSON) — never resume / cover-letter prose, never claim text. Claims are
    summarized as an ``approved/total`` count derived from approval booleans.
  * Writes ONLY ``MANUAL_SUBMISSION.md`` inside the (gitignored) packet dir.
  * Performs no apply / submit / browser / form / account action. The file it
    writes is a checklist; it asserts ``requires_human_submit=True`` and tells
    the human to review and submit manually.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .packet_review import (
    _find_generated,
    _read_json_safe,
    assess_packet,
    load_claims_index,
)
from .utils import repo_root

CHECKLIST_FILENAME = "MANUAL_SUBMISSION.md"

# Default claims bank (same default as packets-review). Only approval booleans
# are read from it; no claim text is ever loaded.
_DEFAULT_CLAIMS = Path("profile/claims/claims-bank.json")

# Packet sub-dirs that are not packet directories.
_SKIP_DIRS = frozenset({"batches", "_suspicious", "manual-drafts"})


def _resolve_posting_url(status: dict, plan: dict, lead: dict) -> str:
    """Best-effort public posting / application URL (recorded, never opened)."""
    for snap in (status.get("routing_snapshot"), plan.get("routing_snapshot")):
        if isinstance(snap, dict) and snap.get("posting_url"):
            return str(snap["posting_url"])
    corr = plan.get("correlation_keys")
    if isinstance(corr, dict):
        for key in ("origin_posting_url", "posting_url"):
            if corr.get(key):
                return str(corr[key])
    for key in ("application_url", "canonical_url", "posting_url"):
        if lead.get(key):
            return str(lead[key])
    return ""


def _asset_block(generated_dir: Path, content_ids: list) -> dict:
    """Resolve resume + cover-letter PDF/markdown paths from generated descriptors.

    Only reads the descriptor JSON (paths + status), never the prose files.
    """
    resume_desc: dict = {}
    cover_desc: dict = {}
    for cid in content_ids:
        if "cover-letter" in str(cid):
            cover_desc = _find_generated(generated_dir, str(cid))
        else:
            resume_desc = _find_generated(generated_dir, str(cid))

    def _one(desc: dict) -> dict:
        return {
            "pdf": desc.get("pdf_path") or None,
            "markdown": desc.get("output_path") or None,
        }

    return {"resume": _one(resume_desc), "cover_letter": _one(cover_desc)}


def _cautions(lead: dict) -> list[str]:
    """Short, non-private posting cautions worth a manual double-check.

    Derived from public posting metadata + posting-fit reason codes only (no
    candidate claim content). Best-effort: returns whatever is available.
    """
    out: list[str] = []
    location = (lead.get("location") or "").strip()
    if location:
        out.append(f"Location: {location} — confirm you meet the location / remote eligibility")
    emp = (lead.get("employment_type") or "").strip()
    if emp:
        out.append(f"Employment type: {emp}")
    fit = lead.get("fit_assessment") or {}
    sen = (fit.get("seniority_reason") or "").strip()
    if sen and sen != "seniority-ok":
        out.append(f"Seniority signal: {sen} — confirm the level matches you")
    comp = (fit.get("compensation_reason") or "").strip()
    if comp and comp not in ("", "compensation-ok"):
        out.append(f"Compensation signal: {comp} — confirm salary expectations manually")
    return out


def build_checklist_data(
    draft_dir: Path,
    *,
    data_root: Path,
    claims_index: dict[str, bool],
    now: datetime | None = None,
) -> dict | None:
    """Assemble the safe, prose-free data backing one packet's checklist.

    Returns None when the directory is not a packet (no plan.json/status.json).
    """
    now = now or datetime.now(timezone.utc)
    rec = assess_packet(
        draft_dir, data_root=data_root, claims_index=claims_index, now=now, dup_map={},
    )
    if rec is None:
        return None

    status = _read_json_safe(draft_dir / "status.json")
    plan = _read_json_safe(draft_dir / "plan.json")
    lead = (
        _read_json_safe(data_root / "leads" / f"{rec['lead_id']}.json")
        if rec.get("lead_id") else {}
    )

    posting_url = _resolve_posting_url(status, plan, lead)
    assets = _asset_block(
        data_root / "generated", status.get("generated_content_ids") or [],
    )

    return {
        "draft_id": rec["draft_id"],
        "lead_id": rec.get("lead_id") or "",
        "company": rec.get("company") or "",
        "title": rec.get("title") or "",
        "posting_url": posting_url,
        "lane": rec.get("lane") or "",
        "tier": rec.get("tier") or "",
        "score": rec.get("score"),
        "score_label": rec.get("score_label") or "",
        "lifecycle_state": rec.get("lifecycle_state") or "unknown",
        "requires_human_submit": rec.get("requires_human_submit") is True,
        "assets": assets,
        "ats": rec.get("ats") or {},
        "claims": rec.get("claims") or {},
        "claim_safety_warnings": rec.get("claim_safety_warnings") or [],
        "cautions": _cautions(lead),
        "generated_at": now.isoformat(),
    }


def _asset_lines(label: str, asset: dict) -> list[str]:
    """Render the PDF (preferred) + markdown-fallback lines for one asset."""
    pdf = asset.get("pdf")
    md = asset.get("markdown")
    lines = []
    if pdf:
        lines.append(f"- {label} PDF (upload this): `{pdf}`")
        if md:
            lines.append(f"  - markdown source (fallback): `{md}`")
    elif md:
        lines.append(f"- {label} PDF: not available — use the markdown fallback below")
        lines.append(f"  - {label} markdown (fallback): `{md}`")
    else:
        lines.append(f"- {label}: not available — regenerate this packet before submitting")
    return lines


def render_checklist(data: dict) -> str:
    """Render the human-facing ``MANUAL_SUBMISSION.md`` markdown (safe metadata)."""
    draft_id = data["draft_id"]
    url = data.get("posting_url") or ""
    url_display = url or "(no posting URL recorded — find it on the company careers page)"
    score = data.get("score")
    score_s = str(score) if score is not None else "?"
    claims = data.get("claims") or {}
    claims_total = claims.get("total", 0)
    claims_approved = claims.get("approved", 0)
    claims_line = f"{claims_approved}/{claims_total} approved"
    if claims_total and not claims.get("all_approved"):
        claims_line += " (NOT all approved — review before submitting)"
    ats = data.get("ats") or {}
    safety_warnings = data.get("claim_safety_warnings") or []

    lines: list[str] = []
    lines.append(f"# Manual submission checklist — {data.get('company') or '?'}")
    lines.append("")
    lines.append(f"**{data.get('title') or '?'}**")
    lines.append("")
    lines.append("> This packet was DRAFTED ONLY. Nothing has been submitted. You must")
    lines.append("> review every field and click Submit yourself on the company site.")
    lines.append("")

    # --- Packet facts -------------------------------------------------------
    lines.append("## Packet")
    lines.append("")
    lines.append(f"- Company: {data.get('company') or '?'}")
    lines.append(f"- Job title: {data.get('title') or '?'}")
    lines.append(f"- Apply / posting URL: {url_display}")
    lines.append(f"- Draft id: `{draft_id}`")
    if data.get("lead_id"):
        lines.append(f"- Lead id: `{data['lead_id']}`")
    lines.append(f"- Resume lane / variant: {data.get('lane') or '?'}")
    lines.append(f"- Score / tier / fit: {score_s} / {data.get('tier') or '?'} / {data.get('score_label') or '?'}")
    lines.append(f"- Lifecycle state: {data.get('lifecycle_state') or '?'}")
    lines.append(f"- requires_human_submit = {data.get('requires_human_submit')}")
    lines.append("")

    # --- Files to upload ----------------------------------------------------
    lines.append("## Files to upload")
    lines.append("")
    assets = data.get("assets") or {}
    lines.extend(_asset_lines("Resume", assets.get("resume") or {}))
    lines.extend(_asset_lines("Cover letter", assets.get("cover_letter") or {}))
    lines.append("")

    # --- Quality signals ----------------------------------------------------
    lines.append("## Quality signals (review these first)")
    lines.append("")
    lines.append(f"- ATS check: {ats.get('errors', 0)} error(s) / {ats.get('warnings', 0)} warning(s)")
    lines.append(f"- Claims approval: {claims_line}")
    if safety_warnings:
        lines.append(f"- Claim-safety: {', '.join(safety_warnings)}")
    for caution in data.get("cautions") or []:
        lines.append(f"- {caution}")
    lines.append("")

    # --- Manual review checklist -------------------------------------------
    lines.append("## Manual review checklist")
    lines.append("")
    lines.append("- [ ] Open the job posting URL above")
    lines.append("- [ ] Confirm the location / remote eligibility")
    lines.append("- [ ] Answer any work-authorization / sponsorship questions yourself")
    lines.append("- [ ] Answer any salary / location questions yourself")
    lines.append("- [ ] Upload the resume PDF (path above)")
    lines.append("- [ ] Upload the cover-letter PDF if the posting asks for one")
    lines.append("- [ ] Read every generated answer in full before submitting")
    lines.append("- [ ] Submit manually only if everything is accurate")
    lines.append("")

    # --- Post-action commands ----------------------------------------------
    lines.append("## After you act, record it (local only — never submits)")
    lines.append("")
    lines.append("```bash")
    lines.append(
        f'python3 scripts/job_hunt.py mark-packet --draft-id {draft_id} '
        f'--status manually_submitted --submitted-url "{url or "<url>"}"'
    )
    lines.append(
        f'python3 scripts/job_hunt.py mark-packet --draft-id {draft_id} '
        f'--status skipped --note "Reason"'
    )
    lines.append(
        f'python3 scripts/job_hunt.py mark-packet --draft-id {draft_id} '
        f'--status needs_revision --note "What needs changing"'
    )
    lines.append(
        f'python3 scripts/job_hunt.py mark-packet --draft-id {draft_id} '
        f'--status follow_up_later --follow-up-date YYYY-MM-DD --note "Reason"'
    )
    lines.append("```")
    lines.append("")

    # --- Safety -------------------------------------------------------------
    lines.append("## Safety")
    lines.append("")
    lines.append("- This packet was drafted only. Nothing has been submitted.")
    lines.append("- A human must review and submit manually on the company's own site.")
    lines.append("- Do not rely on generated answers for sensitive employer questions")
    lines.append("  (work authorization, sponsorship, salary, location) — answer those yourself.")
    lines.append(f"- requires_human_submit = {data.get('requires_human_submit')} (this never changes automatically).")
    lines.append("")

    return "\n".join(lines)


def write_checklist(
    draft_dir: Path,
    *,
    data_root: Path,
    claims_index: dict[str, bool] | None = None,
    claims_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Write ``MANUAL_SUBMISSION.md`` into one packet dir (safe; never submits).

    Returns a result dict: ``{draft_id, written, updated, skipped, reason,
    missing_url, missing_pdf}``. ``skipped`` is True (with ``reason``) when the
    directory is not a packet. Only the checklist file is written.
    """
    draft_dir = Path(draft_dir)
    if claims_index is None:
        claims_index = load_claims_index(
            claims_path if claims_path is not None else (repo_root() / _DEFAULT_CLAIMS)
        )
    data = build_checklist_data(
        draft_dir, data_root=data_root, claims_index=claims_index, now=now,
    )
    if data is None:
        return {
            "draft_id": draft_dir.name,
            "written": False,
            "updated": False,
            "skipped": True,
            "reason": "not_a_packet",
        }
    out_path = draft_dir / CHECKLIST_FILENAME
    existed = out_path.exists()
    out_path.write_text(render_checklist(data), encoding="utf-8")
    assets = data.get("assets") or {}
    missing_pdf = [
        name for name in ("resume", "cover_letter")
        if not (assets.get(name) or {}).get("pdf")
    ]
    return {
        "draft_id": data["draft_id"],
        "written": True,
        "updated": existed,
        "skipped": False,
        "reason": None,
        "missing_url": not data.get("posting_url"),
        "missing_pdf": missing_pdf,
    }


def refresh_checklists(
    *,
    data_root: Path | None = None,
    claims_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Backfill ``MANUAL_SUBMISSION.md`` for every existing packet (safe).

    Writes only the per-packet checklist files (all gitignored). Returns a safe
    aggregate: scanned / written / updated / skipped / missing URLs / missing PDFs.
    Never submits, opens a browser, or prints/returns private prose.
    """
    data_root = data_root or (repo_root() / "data")
    now = now or datetime.now(timezone.utc)
    apps_root = data_root / "applications"
    claims_index = load_claims_index(
        claims_path if claims_path is not None else (repo_root() / _DEFAULT_CLAIMS)
    )
    result = {
        "scanned": 0,
        "written": 0,
        "updated": 0,
        "skipped": 0,
        "missing_url": 0,
        "missing_pdf": 0,
        "drafts_missing_url": [],
        "drafts_missing_pdf": [],
    }
    if not apps_root.is_dir():
        return result
    for d in sorted(apps_root.iterdir()):
        if not d.is_dir() or d.name in _SKIP_DIRS:
            continue
        if not (d / "status.json").exists():
            continue
        result["scanned"] += 1
        res = write_checklist(
            d, data_root=data_root, claims_index=claims_index, now=now,
        )
        if res.get("skipped"):
            result["skipped"] += 1
            continue
        if res.get("updated"):
            result["updated"] += 1
        else:
            result["written"] += 1
        if res.get("missing_url"):
            result["missing_url"] += 1
            result["drafts_missing_url"].append(res["draft_id"])
        if res.get("missing_pdf"):
            result["missing_pdf"] += 1
            result["drafts_missing_pdf"].append(res["draft_id"])
    return result
