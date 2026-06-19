"""Config-driven resume variant registry and lead -> variant routing.

The registry (``config/resume-variants.json``) maps job-title "lanes" to a
pre-authored, ATS-passing resume file. ``route_lead`` scores every variant
against a scored lead with a transparent, inspectable rubric (no model call)
and returns a fully auditable decision artifact.

Routing rubric (per variant, higher = better fit):

    title    -- fraction of the variant's title_patterns present in the lead
                title. The dominant signal. A wildcard lane (empty patterns)
                scores 0 here so any specialized lane that matches outranks it.
    skills   -- fraction of the variant's emphasis_skills the lead covers.
    seniority-- flat credit when the lead's inferred seniority is in the band.

The ``default_variant`` is selected when no specialized lane clears
``MIN_SPECIALIZED_SCORE``. The decision is flagged ``needs_human_review`` when
the chosen resume file is missing on disk, two lanes are within a near-tie, the
winning score is low-confidence, or the lead was never scored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from .schema_checks import ValidationError, validate
from .utils import now_iso, read_json, repo_root

DEFAULT_REGISTRY_REL: Final = "config/resume-variants.json"
REGISTRY_SCHEMA_REL: Final = "schemas/resume-variant-registry.schema.json"
SELECTION_SCHEMA_VERSION: Final = 1

# Rubric weights. Not normalized — comparable across variants of one lead.
TITLE_WEIGHT: Final = 60.0
SKILLS_WEIGHT: Final = 30.0
SENIORITY_WEIGHT: Final = 10.0

# A specialized lane must clear this to be preferred over the default lane.
MIN_SPECIALIZED_SCORE: Final = 22.0
# Top-two specialized lanes within this margin -> ask a human.
NEAR_TIE_MARGIN: Final = 8.0
# A specialized winner below this is "low" confidence.
LOW_CONFIDENCE_SCORE: Final = 38.0


class RegistryError(ValueError):
    """Raised when the resume variant registry is malformed."""


# --- loading -----------------------------------------------------------------

def load_registry(path: Path | None = None) -> dict:
    """Load and validate the resume variant registry.

    Raises ``RegistryError`` if the file is missing, fails schema validation,
    or is internally inconsistent (duplicate ids, unknown default).
    """
    root = repo_root()
    registry_path = path or (root / DEFAULT_REGISTRY_REL)
    if not registry_path.exists():
        raise RegistryError(f"resume variant registry not found: {registry_path}")
    data = read_json(registry_path)
    schema = read_json(root / REGISTRY_SCHEMA_REL)
    try:
        validate(data, schema)
    except ValidationError as exc:
        raise RegistryError(f"registry failed schema validation: {exc}") from exc

    variants = data.get("variants", [])
    ids = [v["id"] for v in variants]
    if len(ids) != len(set(ids)):
        raise RegistryError("registry has duplicate variant ids")
    if data["default_variant"] not in ids:
        raise RegistryError(
            f"default_variant {data['default_variant']!r} is not a defined variant id"
        )
    return data


# --- lead feature extraction -------------------------------------------------

def infer_seniority(title: str) -> str:
    """Infer a coarse seniority band from a job title."""
    t = (title or "").lower()
    if any(k in t for k in ("staff", "principal", "distinguished")):
        return "staff"
    if any(k in t for k in ("senior", "sr.", "sr ", "lead", "architect")):
        return "senior"
    if any(k in t for k in ("junior", "jr.", "jr ", "entry", "associate", "intern", "new grad", "graduate")):
        return "junior"
    return "mid"


def _lead_skill_tokens(lead: dict) -> set[str]:
    """Collect lowercase skill-ish tokens describing the lead."""
    tokens: set[str] = set()
    fit = lead.get("fit_assessment") or {}
    for skill in fit.get("matched_skills", []) or []:
        tokens.add(str(skill).lower())
    req = lead.get("normalized_requirements") or {}
    for bucket in ("required", "preferred"):
        for skill in req.get(bucket, []) or []:
            tokens.add(str(skill).lower())
    # Title words also count toward emphasis overlap (cheap signal).
    for word in (lead.get("title") or "").lower().replace("/", " ").split():
        tokens.add(word.strip(",.()"))
    return {t for t in tokens if t}


# --- scoring -----------------------------------------------------------------

def _score_variant(
    variant: dict, title_lc: str, lead_skills: set[str], lead_seniority: str
) -> tuple[float, list[str], list[str]]:
    patterns = [p.lower() for p in variant.get("title_patterns", [])]
    matched_titles = [p for p in patterns if p in title_lc]
    # title_patterns are alternatives (OR), not a checklist: matching ANY one is
    # a strong routing signal worth full title credit, with a small bonus for
    # additional corroborating matches. A wildcard lane (no patterns) gets 0 so
    # any specialized lane that matches outranks it.
    if not patterns:
        title_component = 0.0
    elif matched_titles:
        title_component = TITLE_WEIGHT + min(len(matched_titles) - 1, 2) * 5.0
    else:
        title_component = 0.0

    emphasis = [s.lower() for s in variant.get("emphasis_skills", [])]
    matched_skills = [s for s in emphasis if s in lead_skills]
    skills_component = (
        SKILLS_WEIGHT * (len(matched_skills) / len(emphasis)) if emphasis else 0.0
    )

    bands = variant.get("seniority_bands", [])
    seniority_component = SENIORITY_WEIGHT if (not bands or lead_seniority in bands) else 0.0

    score = round(title_component + skills_component + seniority_component, 2)
    return score, matched_titles, matched_skills


def _resume_exists(rel_path: str) -> bool:
    if not rel_path:
        return False
    return (repo_root() / rel_path).exists()


# --- routing -----------------------------------------------------------------

def route_lead(lead: dict, registry: dict) -> dict:
    """Route a (preferably scored) lead to its best resume variant.

    Returns a decision dict conforming to ``schemas/resume-selection.schema.json``.
    """
    title = lead.get("title") or ""
    title_lc = title.lower()
    lead_skills = _lead_skill_tokens(lead)
    lead_seniority = infer_seniority(title)
    default_id = registry["default_variant"]

    scored: list[dict] = []
    for variant in registry["variants"]:
        score, matched_titles, matched_skills = _score_variant(
            variant, title_lc, lead_skills, lead_seniority
        )
        scored.append(
            {
                "variant": variant,
                "score": score,
                "matched_titles": matched_titles,
                "matched_skills": matched_skills,
                "is_default": variant["id"] == default_id,
                "resume_exists": _resume_exists(variant.get("resume_path", "")),
            }
        )

    # Stable order preserves registry priority as the tie-break.
    specialized = [s for s in scored if not s["is_default"]]
    specialized_sorted = sorted(specialized, key=lambda s: -s["score"])
    default_entry = next(s for s in scored if s["is_default"])

    best_specialized = specialized_sorted[0] if specialized_sorted else None
    runner_up = specialized_sorted[1] if len(specialized_sorted) > 1 else None

    fallback_used = (
        best_specialized is None or best_specialized["score"] < MIN_SPECIALIZED_SCORE
    )
    chosen = default_entry if fallback_used else best_specialized

    # --- confidence ---
    review_reasons: list[str] = []
    if not lead.get("fit_assessment"):
        review_reasons.append("lead_not_scored")

    if fallback_used:
        confidence = "low"
        if best_specialized and best_specialized["score"] > 0:
            review_reasons.append(
                f"no_specialized_lane_cleared_threshold (best={best_specialized['variant']['id']}"
                f"@{best_specialized['score']})"
            )
    else:
        near_tie = (
            runner_up is not None
            and (best_specialized["score"] - runner_up["score"]) < NEAR_TIE_MARGIN
        )
        if best_specialized["score"] < LOW_CONFIDENCE_SCORE:
            confidence = "low"
            review_reasons.append("low_confidence_score")
        elif near_tie:
            confidence = "medium"
            review_reasons.append(
                f"near_tie_with:{runner_up['variant']['id']}@{runner_up['score']}"
            )
        else:
            confidence = "high"

    if not chosen["resume_exists"]:
        review_reasons.append(
            f"resume_source_missing:{chosen['variant'].get('resume_path', '')}"
        )

    variant = chosen["variant"]
    rationale = _build_rationale(chosen, fallback_used, lead_seniority)

    alternatives = [
        {
            "variant_id": s["variant"]["id"],
            "score": s["score"],
            "resume_exists": s["resume_exists"],
        }
        for s in sorted(scored, key=lambda s: -s["score"])
        if s["variant"]["id"] != variant["id"]
    ]

    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "lead_id": lead.get("lead_id", ""),
        "lead_title": title,
        "selected_variant_id": variant["id"],
        "selected_variant_label": variant.get("label", variant["id"]),
        "selected_resume_path": variant.get("resume_path", ""),
        "selected_resume_exists": chosen["resume_exists"],
        "score": chosen["score"],
        "confidence": confidence,
        "fallback_used": fallback_used,
        "needs_human_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "rationale": rationale,
        "matched_title_patterns": chosen["matched_titles"],
        "matched_emphasis_skills": chosen["matched_skills"],
        "alternatives": alternatives,
        "registry_version": int(registry.get("schema_version", 1)),
        "selected_at": now_iso(),
    }


def _build_rationale(chosen: dict, fallback_used: bool, seniority: str) -> str:
    variant = chosen["variant"]
    if fallback_used:
        return (
            f"No specialized lane cleared the {MIN_SPECIALIZED_SCORE} threshold; "
            f"used default lane '{variant['id']}'. Inferred seniority: {seniority}."
        )
    parts = []
    if chosen["matched_titles"]:
        parts.append(f"title matched {chosen['matched_titles']}")
    if chosen["matched_skills"]:
        parts.append(f"emphasis skills overlap {chosen['matched_skills']}")
    parts.append(f"inferred seniority {seniority}")
    return (
        f"Routed to '{variant['id']}' (score {chosen['score']}): "
        + "; ".join(parts)
        + "."
    )


# --- generation integration --------------------------------------------------

def pick_registry_resume(lead: dict) -> tuple[Path | None, dict | None]:
    """Generation hook: return the routed resume path *only if it exists on disk*.

    Returns ``(path, decision)`` when the routed variant's resume file exists,
    so ``generation.py`` can use the curated file. Returns ``(None, None)`` when
    the registry is absent/malformed or the routed file is missing — letting the
    caller fall through to its legacy lane logic unchanged. Never raises.
    """
    try:
        registry = load_registry()
    except (RegistryError, OSError, ValueError):
        return None, None
    decision = route_lead(lead, registry)
    if decision["selected_resume_exists"]:
        return repo_root() / decision["selected_resume_path"], decision
    return None, decision
