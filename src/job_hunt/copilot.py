"""Recent-job scan and the Level 1.5 application co-pilot orchestrator.

Two surfaces sit on top of the existing discovery/scoring/routing machinery:

``scan_recent``       -- filter already-discovered leads to a wall-clock window
                         (``1h``, ``2d``, an ISO timestamp) and group by fit tier.
``plan_copilot_run``  -- chain scan -> resume-variant routing -> per-job packet
                         plan, emitting ONE decision log per run. Plan/dry-run by
                         construction: it never generates final content and never
                         submits. The human submit gate is preserved by design.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

from .resume_registry import RegistryError, load_registry, route_lead
from .utils import ensure_dir, now_iso, read_json, write_json

SCAN_SCHEMA_VERSION: Final = 1
DECISION_LOG_SCHEMA_VERSION: Final = 1

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS: Final = {"m": 60, "h": 3600, "d": 86400, "w": 604800}

# Higher = stronger fit. Used for --min-tier gating and sort order.
TIER_RANK: Final = {"strong_yes": 3, "maybe": 2, "no": 1, "unscored": 0}


# --- time parsing ------------------------------------------------------------

def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Resolve a ``--since`` value to an absolute UTC window-start.

    Accepts relative durations (``30m``, ``1h``, ``2d``, ``1w``) or an ISO
    timestamp. Raises ``ValueError`` on anything else.
    """
    now = now or datetime.now(timezone.utc)
    match = _DURATION_RE.match(value or "")
    if match:
        qty = int(match.group(1))
        unit = match.group(2).lower()
        return now - timedelta(seconds=qty * _UNIT_SECONDS[unit])
    iso = _parse_iso(value)
    if iso is not None:
        return iso
    raise ValueError(
        f"--since must be a duration like '1h', '30m', '2d', '1w' or an ISO "
        f"timestamp; got {value!r}"
    )


# --- lead feature extraction -------------------------------------------------

def lead_effective_timestamp(lead: dict) -> datetime | None:
    """Newest timestamp describing when this lead was seen/posted.

    Considers per-source ``discovered_at``, top-level ``discovered_at`` /
    ``ingested_at``, and ``listing_updated_at``. Returns ``None`` when nothing
    parses (lead is then treated as outside any recent window).
    """
    candidates: list[datetime] = []
    for source in lead.get("observed_sources", []) or []:
        for key in ("discovered_at", "listing_updated_at"):
            dt = _parse_iso(str(source.get(key) or ""))
            if dt is not None:
                candidates.append(dt)
    for key in ("discovered_at", "ingested_at", "listing_updated_at"):
        dt = _parse_iso(str(lead.get(key) or ""))
        if dt is not None:
            candidates.append(dt)
    return max(candidates) if candidates else None


def lead_tier(lead: dict) -> tuple[str, float | None]:
    """Return ``(tier, fit_score)`` for a lead from its fit_assessment."""
    fit = lead.get("fit_assessment")
    if not fit:
        return "unscored", None
    rec = fit.get("fit_recommendation", "unscored")
    if rec not in TIER_RANK:
        rec = "unscored"
    return rec, fit.get("fit_score")


# --- lead loading ------------------------------------------------------------

def load_leads(leads_dir: Path) -> list[tuple[Path, dict]]:
    """Load every ``*.json`` lead in ``leads_dir`` (sorted, skipping unreadable)."""
    out: list[tuple[Path, dict]] = []
    if not leads_dir.exists():
        return out
    for path in sorted(leads_dir.glob("*.json")):
        try:
            lead = read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(lead, dict) and lead.get("lead_id"):
            out.append((path, lead))
    return out


# --- recent scan -------------------------------------------------------------

def _candidate(path: Path, lead: dict, effective: datetime) -> dict:
    tier, fit_score = lead_tier(lead)
    return {
        "lead_id": lead.get("lead_id", ""),
        "title": lead.get("title", ""),
        "company": lead.get("company", ""),
        "source": lead.get("source", ""),
        "application_url": lead.get("application_url", ""),
        "tier": tier,
        "fit_score": fit_score,
        "effective_timestamp": effective.astimezone(timezone.utc).isoformat(),
        "lead_path": str(path),
    }


def filter_recent_leads(
    leads: list[tuple[Path, dict]], window_start: datetime
) -> list[dict]:
    """Return candidate dicts for leads whose effective timestamp >= window_start."""
    candidates: list[dict] = []
    for path, lead in leads:
        effective = lead_effective_timestamp(lead)
        if effective is None or effective < window_start:
            continue
        candidates.append(_candidate(path, lead, effective))
    candidates.sort(
        key=lambda c: (TIER_RANK.get(c["tier"], 0), c["effective_timestamp"]),
        reverse=True,
    )
    return candidates


def top_candidates(candidates: list[dict], n: int) -> list[dict]:
    """Top ``n`` candidates ranked by fit (skillset match), then recency.

    Used by the ``scan-recent-jobs --top N`` brief view. Scored leads outrank
    unscored ones (a missing ``fit_score`` sorts last). Pure/total-order so the
    result is stable for testing; ``n <= 0`` returns an empty list.
    """
    def _key(c: dict) -> tuple:
        score = c.get("fit_score")
        score = score if isinstance(score, (int, float)) else -1
        return (score, c.get("effective_timestamp") or "")

    return sorted(candidates, key=_key, reverse=True)[:max(0, n)]


def scan_recent(
    leads_dir: Path,
    since: str,
    *,
    now: datetime | None = None,
    discover_ran: bool = False,
) -> dict:
    """Build a recent-scan artifact (schemas/recent-scan.schema.json)."""
    window_start = parse_since(since, now=now)
    leads = load_leads(leads_dir)
    candidates = filter_recent_leads(leads, window_start)
    counts = {
        "total_in_window": len(candidates),
        "strong_yes": sum(1 for c in candidates if c["tier"] == "strong_yes"),
        "maybe": sum(1 for c in candidates if c["tier"] == "maybe"),
        "no": sum(1 for c in candidates if c["tier"] == "no"),
        "unscored": sum(1 for c in candidates if c["tier"] == "unscored"),
    }
    return {
        "schema_version": SCAN_SCHEMA_VERSION,
        "scanned_at": now_iso(),
        "since": since,
        "window_start": window_start.astimezone(timezone.utc).isoformat(),
        "leads_dir": str(leads_dir),
        "discover_ran": discover_ran,
        "counts": counts,
        "candidates": candidates,
    }


# --- orchestrator ------------------------------------------------------------

def _next_commands(lead_path: str) -> list[str]:
    return [
        f"python3 scripts/job_hunt.py select-resume-variant --lead {lead_path}",
        f"python3 scripts/job_hunt.py prepare-application --lead {lead_path}",
        "python3 scripts/job_hunt.py apply-posting --draft-id <draft-id-from-prepare>",
    ]


def plan_copilot_run(
    leads_dir: Path,
    since: str,
    *,
    min_tier: str = "maybe",
    registry: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Plan a co-pilot run: scan -> route variant -> per-job packet plan.

    Returns a decision-log dict. Does NOT write files, generate content, or
    submit anything. Every job below the human submit gate.
    """
    if min_tier not in TIER_RANK:
        raise ValueError(f"--min-tier must be one of {sorted(TIER_RANK)}; got {min_tier!r}")
    scan = scan_recent(leads_dir, since, now=now)
    if registry is None:
        registry = load_registry()

    threshold = TIER_RANK[min_tier]
    leads_by_id = {lead.get("lead_id"): (path, lead) for path, lead in load_leads(leads_dir)}

    jobs: list[dict] = []
    for cand in scan["candidates"]:
        if TIER_RANK.get(cand["tier"], 0) < threshold:
            continue
        path, lead = leads_by_id.get(cand["lead_id"], (None, None))
        if lead is None:
            continue
        selection = route_lead(lead, registry)
        fit = lead.get("fit_assessment") or {}
        review_items: list[str] = list(selection["review_reasons"])
        if cand["tier"] == "maybe":
            review_items.append("tier=maybe — confirm fit before preparing packet")
        jobs.append(
            {
                "lead_id": cand["lead_id"],
                "title": cand["title"],
                "company": cand["company"],
                "application_url": cand["application_url"],
                "tier": cand["tier"],
                "fit_score": cand["fit_score"],
                "why_matched": fit.get("fit_rationale", "no fit_assessment on lead"),
                "resume_selection": selection,
                "next_commands": _next_commands(cand["lead_path"]),
                "needs_human_review": bool(review_items),
                "review_items": review_items,
            }
        )

    return {
        "schema_version": DECISION_LOG_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "since": since,
        "window_start": scan["window_start"],
        "min_tier": min_tier,
        "leads_dir": str(leads_dir),
        "scan_counts": scan["counts"],
        "jobs_planned": len(jobs),
        "jobs_needing_review": sum(1 for j in jobs if j["needs_human_review"]),
        "human_gate": (
            "Co-pilot prepares applications up to a filled-but-unsubmitted form. "
            "Final Submit always requires a human click (auto_submit_tiers = [])."
        ),
        "jobs": jobs,
    }


def render_decision_log_md(run: dict) -> str:
    """Human-readable decision log."""
    lines: list[str] = []
    lines.append("# Co-Pilot Run Decision Log")
    lines.append("")
    lines.append(f"- Generated: {run['generated_at']}")
    lines.append(f"- Window: leads since `{run['since']}` (>= {run['window_start']})")
    lines.append(f"- Min tier: `{run['min_tier']}`")
    c = run["scan_counts"]
    lines.append(
        f"- In window: {c['total_in_window']} "
        f"(strong_yes={c['strong_yes']}, maybe={c['maybe']}, "
        f"no={c['no']}, unscored={c['unscored']})"
    )
    lines.append(f"- Jobs planned: {run['jobs_planned']} "
                 f"({run['jobs_needing_review']} need review)")
    lines.append("")
    lines.append(f"> **Human gate:** {run['human_gate']}")
    lines.append("")
    if not run["jobs"]:
        lines.append("_No jobs at or above the minimum tier in this window._")
        return "\n".join(lines) + "\n"
    for i, job in enumerate(run["jobs"], 1):
        sel = job["resume_selection"]
        lines.append(f"## {i}. {job['title']} — {job['company']}")
        lines.append("")
        lines.append(f"- Lead: `{job['lead_id']}`")
        lines.append(f"- Fit: **{job['tier']}** (score {job['fit_score']})")
        lines.append(f"- Why matched: {job['why_matched']}")
        lines.append(
            f"- Resume variant: **{sel['selected_variant_id']}** "
            f"(confidence {sel['confidence']}, score {sel['score']}"
            f"{', fallback' if sel['fallback_used'] else ''})"
        )
        lines.append(f"  - {sel['rationale']}")
        lines.append(f"  - Resume file: `{sel['selected_resume_path']}` "
                     f"(exists: {sel['selected_resume_exists']})")
        if job["needs_human_review"]:
            lines.append(f"- ⚠️ Needs human review: {'; '.join(job['review_items'])}")
        else:
            lines.append("- ✅ No blocking review items")
        lines.append("- Next steps:")
        for cmd in job["next_commands"]:
            lines.append(f"  - `{cmd}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_copilot_run(run: dict, runs_root: Path) -> Path:
    """Persist decision-log.json + decision-log.md under runs_root/copilot-<ts>/."""
    stamp = run["generated_at"].replace(":", "").replace("-", "").replace("+", "")[:15]
    run_dir = runs_root / f"copilot-{stamp}"
    ensure_dir(run_dir)
    write_json(run_dir / "decision-log.json", run)
    (run_dir / "decision-log.md").write_text(render_decision_log_md(run), encoding="utf-8")
    return run_dir
