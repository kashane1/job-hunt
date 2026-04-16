"""Pipeline analytics: shared aggregator + reports on applications × leads × companies.

The aggregator joins every application status record with its lead and (if
present) company research and selected generated content, returning a flat
list of AggregatedRow TypedDicts. Three reports consume the same shape,
ensuring no divergence across consumers.

Reports:
- report_dashboard: apps per week, stage counts, callback rate, variant win
  rates, stage-to-stage conversions. Sample-size gates (insufficient_data /
  low / ok) per batch 2 plan.
- report_skills_gap: missing skills across scored leads, canonicalized via
  skills-taxonomy, filtered through profile/skills-excluded.yaml.
- report_rejection_patterns: drop-off stages, industry/company patterns,
  missing-skill correlations for rejected applications (ghosted tracked
  separately).

Internal module — raises ValueError directly per batch 1 convention.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NotRequired, TypedDict

from .utils import load_yaml_file, read_json

# Sample-size thresholds per batch 2 plan / agent-native review:
# <10 apps: only report raw counts (avoid misleading rates)
# 10-29 apps: report rates with confidence="low"
# 30+ apps: confidence="ok"
MIN_SAMPLE_FOR_RATES: Final = 10
MIN_SAMPLE_FOR_CONFIDENCE: Final = 30

# Callback stages — applications that progressed past the initial submit
CALLBACK_STAGES: Final = frozenset({"phone_screen", "technical", "onsite", "offer", "accepted"})
TERMINAL_STAGES: Final = frozenset({"accepted", "rejected", "withdrawn", "ghosted"})

# Stage ordering for conversion rate tracking
STAGE_SEQUENCE: Final = ("applied", "phone_screen", "technical", "onsite", "offer", "accepted")


class AggregatedRow(TypedDict):
    """Canonical output shape of build_aggregator.

    All three reports consume this type. Locking the shape prevents silent
    divergence across consumers (python review insight).
    """
    lead_id: str
    current_stage: str
    transitions: list[dict]
    applied_date: NotRequired[str | None]
    lead_title: str
    lead_company: str
    fit_score: NotRequired[float | None]
    matched_skills: NotRequired[list[str]]
    missing_skills: NotRequired[list[str]]
    company_stage: NotRequired[str | None]
    company_industry: NotRequired[str | None]
    company_remote_policy: NotRequired[str | None]
    company_fit_score: NotRequired[float | None]
    selected_variant_style: NotRequired[str | None]
    generated_content_ids: NotRequired[list[str]]


def _applied_date(transitions: list[dict]) -> str | None:
    """Find the timestamp of the first transition to 'applied'."""
    for t in transitions:
        if t.get("to_stage") == "applied":
            return t.get("timestamp")
    return None


def _load_leads_by_id(leads_dir: Path) -> dict[str, dict]:
    """Load every lead file into {lead_id: lead_dict}. O(1) lookup per join."""
    by_id: dict[str, dict] = {}
    if not leads_dir.exists():
        return by_id
    for p in leads_dir.glob("*.json"):
        try:
            lead = read_json(p)
            by_id[lead.get("lead_id", p.stem)] = lead
        except (json.JSONDecodeError, KeyError):
            continue
    return by_id


def _load_companies_by_id(companies_dir: Path) -> dict[str, dict]:
    """Load every company research file into {company_id: company_dict}."""
    by_id: dict[str, dict] = {}
    if not companies_dir.exists():
        return by_id
    for p in companies_dir.glob("*.json"):
        try:
            company = read_json(p)
            by_id[company.get("company_id", p.stem)] = company
        except (json.JSONDecodeError, KeyError):
            continue
    return by_id


def _load_content_by_id(content_root: Path) -> dict[str, dict]:
    """Load every generated-content file into {content_id: content_dict}."""
    by_id: dict[str, dict] = {}
    if not content_root.exists():
        return by_id
    for subdir in ("resumes", "cover-letters", "answers", "follow-ups"):
        subpath = content_root / subdir
        if not subpath.exists():
            continue
        for p in subpath.glob("*.json"):
            try:
                content = read_json(p)
                by_id[content.get("content_id", p.stem)] = content
            except (json.JSONDecodeError, KeyError):
                continue
    return by_id


def build_aggregator(data_root: Path) -> tuple[list[AggregatedRow], dict[str, int]]:
    """Join applications × leads × companies × content via dict-based lookups.

    Returns (rows, missing_refs) where missing_refs counts dangling references
    so reports can surface them in their headers (never silently drop records).
    """
    leads_by_id = _load_leads_by_id(data_root / "leads")
    companies_by_id = _load_companies_by_id(data_root / "companies")
    content_by_id = _load_content_by_id(data_root / "generated")

    missing_refs = {
        "missing_lead_refs": 0,
        "missing_company_refs": 0,
        "missing_content_refs": 0,
    }

    rows: list[AggregatedRow] = []
    status_dir = data_root / "applications"
    if not status_dir.exists():
        return rows, missing_refs

    for p in status_dir.glob("*-status.json"):
        try:
            status = read_json(p)
        except (json.JSONDecodeError, KeyError):
            continue
        lead_id = status.get("lead_id", "")
        lead = leads_by_id.get(lead_id)
        if lead is None:
            missing_refs["missing_lead_refs"] += 1
            # Include the row anyway with defaults — never silently drop
            row: AggregatedRow = {
                "lead_id": lead_id,
                "current_stage": status.get("current_stage", ""),
                "transitions": status.get("transitions", []),
                "lead_title": "",
                "lead_company": "",
            }
            rows.append(row)
            continue

        # Company join
        company_id = lead.get("company_research_id", "")
        company = companies_by_id.get(company_id) if company_id else None
        if company_id and company is None:
            missing_refs["missing_company_refs"] += 1

        # Content join — use the first generated_content_id as the "selected" one
        generated_ids = status.get("generated_content_ids", [])
        selected_style: str | None = None
        for cid in generated_ids:
            content = content_by_id.get(cid)
            if content is None:
                missing_refs["missing_content_refs"] += 1
                continue
            if content.get("content_type") == "resume":
                selected_style = content.get("variant_style")
                break

        fit_assessment = lead.get("fit_assessment") or {}
        row = {
            "lead_id": lead_id,
            "current_stage": status.get("current_stage", ""),
            "transitions": status.get("transitions", []),
            "applied_date": _applied_date(status.get("transitions", [])),
            "lead_title": lead.get("title", ""),
            "lead_company": lead.get("company", ""),
            "fit_score": fit_assessment.get("fit_score"),
            "matched_skills": fit_assessment.get("matched_skills", []),
            "missing_skills": fit_assessment.get("missing_skills", []),
            "generated_content_ids": generated_ids,
            "selected_variant_style": selected_style,
        }
        if company is not None:
            row["company_stage"] = company.get("stage")
            row["company_industry"] = company.get("industry")
            row["company_remote_policy"] = company.get("remote_policy")
            row["company_fit_score"] = company.get("company_fit_score")
        rows.append(row)
    return rows, missing_refs


# =============================================================================
# Dashboard report
# =============================================================================

def _weekly_counts(rows: list[AggregatedRow]) -> dict[str, int]:
    """ISO week label → count of applications applied that week."""
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        applied = r.get("applied_date")
        if not applied:
            continue
        try:
            dt = datetime.fromisoformat(applied.replace("Z", "+00:00"))
        except ValueError:
            continue
        iso_year, iso_week, _ = dt.isocalendar()
        counts[f"{iso_year}-W{iso_week:02d}"] += 1
    return dict(counts)


def _stage_conversions(rows: list[AggregatedRow]) -> dict[str, dict]:
    """For each adjacent stage pair in STAGE_SEQUENCE, compute conversion rate.

    A row "reached" a stage if any transition has to_stage == that stage.
    """
    reached_counts: dict[str, int] = {s: 0 for s in STAGE_SEQUENCE}
    for r in rows:
        reached_stages = {t.get("to_stage") for t in r.get("transitions", [])}
        for s in STAGE_SEQUENCE:
            if s in reached_stages:
                reached_counts[s] += 1
    conversions: dict[str, dict] = {}
    for i in range(len(STAGE_SEQUENCE) - 1):
        from_s, to_s = STAGE_SEQUENCE[i], STAGE_SEQUENCE[i + 1]
        reached_from = reached_counts[from_s]
        reached_to = reached_counts[to_s]
        conversions[f"{from_s}_to_{to_s}"] = {
            "from": reached_from,
            "to": reached_to,
            "rate": round(reached_to / reached_from, 3) if reached_from else 0.0,
        }
    return conversions


def _variant_rates(rows: list[AggregatedRow]) -> dict[str, dict]:
    """Group rows by selected_variant_style; compute callback rate per style."""
    by_variant: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "callbacks": 0})
    for r in rows:
        style = r.get("selected_variant_style") or "unknown"
        by_variant[style]["total"] += 1
        if r["current_stage"] in CALLBACK_STAGES:
            by_variant[style]["callbacks"] += 1
    return {
        style: {
            "total": v["total"],
            "callbacks": v["callbacks"],
            "callback_rate": round(v["callbacks"] / v["total"], 3) if v["total"] else 0.0,
        }
        for style, v in by_variant.items()
    }


def report_dashboard(
    data_root: Path,
    since: str = "",
    weeks: int | None = None,
) -> dict:
    """Application velocity dashboard.

    - since: ISO date string (e.g. "2026-04-01"); filter rows with applied_date >= since
    - weeks: int; shorthand for since = today - N weeks

    Returns structured JSON with confidence field signaling sample-size
    adequacy. Per batch 2 plan Phase 4: confidence=insufficient_data (<10),
    low (10-29), ok (30+).
    """
    rows, missing_refs = build_aggregator(data_root)
    now = datetime.now(UTC)
    if weeks is not None:
        cutoff = now.replace(tzinfo=UTC) - (weeks * __import__("datetime").timedelta(days=7) if False else __import__("datetime").timedelta(weeks=weeks))
        cutoff_str = cutoff.replace(microsecond=0).isoformat()
        if not since or cutoff_str > since:
            since = cutoff_str
    if since:
        rows = [r for r in rows if (r.get("applied_date") or "") >= since]

    total = len(rows)
    stages = Counter(r["current_stage"] for r in rows)

    if total < MIN_SAMPLE_FOR_RATES:
        return {
            "generated_at": now.replace(microsecond=0).isoformat(),
            "sample_size": total,
            "confidence": "insufficient_data",
            "raw_counts": dict(stages),
            "missing_refs": missing_refs,
            "guidance": (
                f"{total} applications. Need at least {MIN_SAMPLE_FOR_RATES} "
                f"before rates are meaningful. Ingest more leads and apply."
            ),
        }

    callbacks = sum(stages[s] for s in CALLBACK_STAGES if s in stages)
    terminals = sum(stages[s] for s in TERMINAL_STAGES if s in stages)

    return {
        "generated_at": now.replace(microsecond=0).isoformat(),
        "sample_size": total,
        "confidence": "low" if total < MIN_SAMPLE_FOR_CONFIDENCE else "ok",
        "stage_counts": dict(stages),
        "callback_rate": round(callbacks / total, 3) if total else 0.0,
        "terminal_rate": round(terminals / total, 3) if total else 0.0,
        "variant_rates": _variant_rates(rows),
        "stage_conversions": _stage_conversions(rows),
        "applications_per_week": _weekly_counts(rows),
        "missing_refs": missing_refs,
    }


# =============================================================================
# Skills gap analyzer (Phase 5a)
# =============================================================================

MIN_SCORED_LEADS_FOR_GAP: Final = 10


def _load_taxonomy(taxonomy_path: Path | None) -> dict[str, str]:
    """Return alias → canonical mapping. Reuses batch 1's SKILL_ALIASES dict
    if no taxonomy file exists, so the two sources stay in sync by default."""
    if taxonomy_path and taxonomy_path.exists():
        data = load_yaml_file(taxonomy_path)
        aliases = data.get("aliases") or {}
        flat: dict[str, str] = {}
        for canonical, alias_list in aliases.items():
            for alias in alias_list:
                flat[str(alias).lower()] = str(canonical).lower()
            flat[str(canonical).lower()] = str(canonical).lower()
        return flat
    # Fallback to batch 1's SKILL_ALIASES
    from . import core
    flat = {}
    for canonical, alias_tuple in core.SKILL_ALIASES.items():
        for alias in alias_tuple:
            flat[str(alias).lower()] = str(canonical).lower()
        flat[str(canonical).lower()] = str(canonical).lower()
    return flat


def _load_exclusions(excluded_path: Path | None) -> set[str]:
    """Return a set of lowercased excluded skill names. Missing file = empty set."""
    if not excluded_path or not excluded_path.exists():
        return set()
    data = load_yaml_file(excluded_path)
    return {str(s).lower() for s in (data.get("excluded") or [])}


def report_skills_gap(
    data_root: Path,
    profile: dict,
    taxonomy_path: Path | None = None,
    excluded_path: Path | None = None,
) -> dict:
    """Aggregate missing skills across scored leads, canonicalize, rank by
    frequency × avg_fit_score.

    Batch 2 Phase 5 review insight: precompute fit_by_lead_id dict to avoid
    O(n²) scan (one of the performance review findings).
    """
    rows, missing_refs = build_aggregator(data_root)
    scored = [r for r in rows if r.get("fit_score") is not None]
    if len(scored) < MIN_SCORED_LEADS_FOR_GAP:
        return {
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "sample_size": len(scored),
            "confidence": "insufficient_data",
            "guidance": (
                f"Need >= {MIN_SCORED_LEADS_FOR_GAP} scored leads; have {len(scored)}. "
                f"Score more leads via score-lead before running skills-gap analysis."
            ),
            "missing_refs": missing_refs,
        }

    taxonomy = _load_taxonomy(taxonomy_path)
    excluded = _load_exclusions(excluded_path)
    profile_skills = {
        taxonomy.get(str(s["name"]).lower(), str(s["name"]).lower())
        for s in profile.get("skills", [])
    }

    gap_counter: Counter[str] = Counter()
    gap_evidence: dict[str, list[str]] = defaultdict(list)
    for row in scored:
        for raw_skill in row.get("missing_skills", []):
            canonical = taxonomy.get(str(raw_skill).lower(), str(raw_skill).lower())
            if canonical in profile_skills or canonical in excluded:
                continue
            gap_counter[canonical] += 1
            gap_evidence[canonical].append(row["lead_id"])

    # Precompute fit lookup — O(1) per gap × evidence (performance review fix)
    fit_by_lead_id = {r["lead_id"]: r["fit_score"] for r in scored if r.get("fit_score") is not None}

    ranked: list[dict] = []
    for skill, count in gap_counter.most_common():
        evidence_leads = gap_evidence[skill]
        fit_values = [fit_by_lead_id.get(lid, 0) or 0 for lid in evidence_leads]
        avg_fit = statistics.mean(fit_values) if fit_values else 0.0
        ranked.append({
            "skill": skill,
            "frequency": count,
            "avg_fit_score": round(avg_fit, 1),
            "priority_score": round(count * avg_fit / 100, 2),
            "evidence_lead_ids": evidence_leads[:10],
        })

    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sample_size": len(scored),
        "confidence": "low" if len(scored) < MIN_SAMPLE_FOR_CONFIDENCE else "ok",
        "profile_skill_count": len(profile_skills),
        "excluded_count": len(excluded),
        "gaps": sorted(ranked, key=lambda x: -x["priority_score"]),
        "missing_refs": missing_refs,
    }


# =============================================================================
# Rejection pattern analyzer (Phase 5b)
# =============================================================================

MIN_TERMINAL_FOR_REJECTION: Final = 10


def _last_non_terminal_stage(transitions: list[dict]) -> str:
    """Walk transitions to find the last stage before a terminal transition."""
    last_live = "applied"
    for t in transitions:
        to_stage = t.get("to_stage", "")
        if to_stage in TERMINAL_STAGES:
            return last_live
        last_live = to_stage
    return last_live


def report_rejection_patterns(data_root: Path) -> dict:
    """Identify patterns in terminal applications (rejected, ghosted, withdrawn).

    Ghosted is tracked SEPARATELY from rejected — they measure different things.
    Observations are factual (percentages, top-N); no prescriptive advice.
    """
    rows, missing_refs = build_aggregator(data_root)
    terminal = [r for r in rows if r["current_stage"] in TERMINAL_STAGES]
    if len(terminal) < MIN_TERMINAL_FOR_REJECTION:
        return {
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "sample_size": len(terminal),
            "confidence": "insufficient_data",
            "guidance": (
                f"Need >= {MIN_TERMINAL_FOR_REJECTION} terminal applications; "
                f"have {len(terminal)}."
            ),
            "missing_refs": missing_refs,
        }

    rejected = [r for r in terminal if r["current_stage"] == "rejected"]
    ghosted = [r for r in terminal if r["current_stage"] == "ghosted"]
    withdrawn = [r for r in terminal if r["current_stage"] == "withdrawn"]

    drop_off: Counter[str] = Counter()
    for r in rejected:
        drop_off[_last_non_terminal_stage(r["transitions"])] += 1

    by_industry = Counter(r.get("company_industry") or "unknown" for r in rejected)
    by_stage = Counter(r.get("company_stage") or "unknown" for r in rejected)
    by_remote = Counter(r.get("company_remote_policy") or "unknown" for r in rejected)

    rejected_missing: Counter[str] = Counter()
    for r in rejected:
        for skill in r.get("missing_skills", []):
            rejected_missing[str(skill).lower()] += 1

    observations: list[str] = []
    total_closed = len(rejected) + len(ghosted)
    if total_closed:
        ghost_rate = len(ghosted) / total_closed
        if ghost_rate > 0.5:
            observations.append(
                f"{round(ghost_rate * 100)}% of closed applications were ghosted. "
                f"Consider whether applications are reaching a human reviewer."
            )
    if rejected and drop_off.get("applied", 0) / len(rejected) > 0.7:
        observations.append(
            "More than 70% of rejections happened at the applied stage (no phone screen). "
            "Likely a resume/keyword fit issue; see analyze-skills-gap."
        )

    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sample_size": len(terminal),
        "confidence": "low" if len(terminal) < MIN_SAMPLE_FOR_CONFIDENCE else "ok",
        "breakdown": {
            "rejected": len(rejected),
            "ghosted": len(ghosted),
            "withdrawn": len(withdrawn),
        },
        "drop_off_by_stage": dict(drop_off),
        "rejected_by_industry": dict(by_industry),
        "rejected_by_company_stage": dict(by_stage),
        "rejected_by_remote_policy": dict(by_remote),
        "top_missing_skills_in_rejected": rejected_missing.most_common(10),
        "observations": observations,
        "missing_refs": missing_refs,
    }
