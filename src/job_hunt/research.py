"""Company research enrichment and fit scoring."""

from __future__ import annotations

import re
from pathlib import Path

from .utils import ensure_dir, now_iso, read_json, slugify, write_json


def research_company(
    company_name: str,
    output_dir: Path,
    existing_research: dict | None = None,
) -> dict:
    """Create a company research scaffold with pre-filled fields where possible."""
    ensure_dir(output_dir)
    company_id = slugify(company_name)
    ts = now_iso()

    if existing_research:
        research = {**existing_research, "researched_at": ts}
    else:
        research = {
            "company_id": company_id,
            "company_name": company_name,
            "researched_at": ts,
            "size_estimate": "",
            "stage": "unknown",
            "industry": "",
            "tech_stack": [],
            "remote_policy": "",
            "glassdoor_rating": 0,
            "funding_info": "",
            "headquarters": "",
            "recent_news": [],
            "source_urls": [],
            "confidence": "low",
            "company_fit_score": 0,
            "company_fit_breakdown": {
                "remote_match": 0,
                "size_match": 0,
                "tech_stack_match": 0,
                "industry_match": 0,
            },
            "notes": "",
        }

    write_json(output_dir / f"{company_id}.json", research)
    return research


def research_company_from_lead(
    lead: dict,
    output_dir: Path,
) -> dict:
    """Create company research from a lead, extracting hints from the job description."""
    company_name = lead.get("company", "Unknown")
    research = research_company(company_name, output_dir)

    # Extract hints from lead.
    raw = lead.get("raw_description", "").lower()
    location = lead.get("location", "")

    # Remote policy hints.
    if any(kw in raw for kw in ("fully remote", "remote-first", "remote friendly")):
        research["remote_policy"] = "remote"
    elif "hybrid" in raw:
        research["remote_policy"] = "hybrid"
    elif "on-site" in raw or "onsite" in raw:
        research["remote_policy"] = "onsite"

    if location:
        research["headquarters"] = location

    # Tech stack from requirements.
    reqs = lead.get("normalized_requirements", {})
    tech_hints = reqs.get("required", []) + reqs.get("preferred", [])
    if tech_hints:
        research["tech_stack"] = tech_hints[:10]

    write_json(output_dir / f"{research['company_id']}.json", research)
    return research


def score_company_fit(
    company: dict,
    candidate_profile: dict,
    scoring_config: dict | None = None,
) -> dict:
    """Score company fit on 4 dimensions, 0-100 total.

    Dimensions and default weights:
    - remote_match: 30
    - size_match: 20
    - tech_stack_match: 30
    - industry_match: 20
    """
    config = scoring_config or {}
    weights = {
        "remote_match": config.get("remote_weight", 30),
        "size_match": config.get("size_weight", 20),
        "tech_stack_match": config.get("tech_stack_weight", 30),
        "industry_match": config.get("industry_weight", 20),
    }

    preferences = candidate_profile.get("preferences", {})
    skills = {s.get("name", "").lower() for s in candidate_profile.get("skills", [])}
    breakdown: dict[str, float] = {}

    # Remote match.
    company_remote = (company.get("remote_policy") or "").lower()
    pref_remote = (preferences.get("remote_preference") or "").lower()
    if company_remote and pref_remote:
        if company_remote == pref_remote:
            breakdown["remote_match"] = weights["remote_match"]
        elif company_remote in ("remote", "hybrid") and pref_remote in ("remote", "hybrid"):
            breakdown["remote_match"] = weights["remote_match"] * 0.6
        else:
            breakdown["remote_match"] = 0
    else:
        breakdown["remote_match"] = weights["remote_match"] * 0.5  # Unknown — neutral.

    # Size match (if preferences define a size pref — for now, neutral).
    pref_size = preferences.get("preferred_company_size", "")
    company_size = company.get("size_estimate", "")
    if pref_size and company_size:
        breakdown["size_match"] = weights["size_match"] if pref_size.lower() in company_size.lower() else 0
    else:
        breakdown["size_match"] = weights["size_match"] * 0.5

    # Tech stack match — Jaccard-like overlap.
    company_tech = {t.lower() for t in company.get("tech_stack", [])}
    if company_tech and skills:
        overlap = len(company_tech & skills)
        total = len(company_tech)
        breakdown["tech_stack_match"] = weights["tech_stack_match"] * min(overlap / max(total, 1), 1.0)
    else:
        breakdown["tech_stack_match"] = weights["tech_stack_match"] * 0.3

    # Industry match.
    company_industry = (company.get("industry") or "").lower()
    pref_industries = [i.lower() for i in preferences.get("preferred_industries", [])]
    if company_industry and pref_industries:
        breakdown["industry_match"] = weights["industry_match"] if any(
            ind in company_industry for ind in pref_industries) else 0
    else:
        breakdown["industry_match"] = weights["industry_match"] * 0.5

    total_score = sum(breakdown.values())

    return {
        "company_fit_score": round(total_score, 1),
        "company_fit_breakdown": {k: round(v, 1) for k, v in breakdown.items()},
    }
