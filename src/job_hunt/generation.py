"""Content generation for resumes, cover letters, and answer sets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .utils import ensure_dir, now_iso, repo_root, short_hash, slugify, tokens, write_json

# --- Resume variant styles ---

STYLE_TECHNICAL_DEPTH = "technical_depth"
STYLE_IMPACT_FOCUSED = "impact_focused"
STYLE_BREADTH = "breadth"

# Variant preference phrases (multi-word to avoid false positives).
VARIANT_BOOST_PHRASES: dict[str, list[str]] = {
    STYLE_TECHNICAL_DEPTH: [
        "system design", "architecture", "migration", "data model",
        "optimization", "infrastructure", "scalability", "distributed",
    ],
    STYLE_IMPACT_FOCUSED: [
        "business impact", "revenue", "cost reduction", "user adoption",
        "time saved", "growth", "retention", "efficiency",
    ],
    STYLE_BREADTH: [
        "frontend", "backend", "infrastructure", "leadership",
        "api integration", "full stack", "cross-functional", "mentoring",
    ],
}


# Curated resume lanes — pre-written, ATS-passing resumes authored in the
# resume-rehab session. When a lead's title matches a lane, the generator
# uses the curated markdown verbatim instead of rendering from the thin
# template, which produces 190-word keyword-stuffed output that fails ATS.
# Fallback to the template only when no lane matches.
CURATED_RESUME_LANES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    # (title_keyword_tuple, curated_resume_relative_path)
    (
        ("ai engineer", "ai systems", "applied ai", "machine learning",
         "ml engineer", "llm", "genai"),
        "data/generated/resumes/kashane-sakhakorn-ai-engineer-2026-04-17.md",
    ),
    # Default lane — picks up everything else (backend, full-stack,
    # platform, generic SWE). Must be last.
    (
        (),  # empty tuple → wildcard
        "data/generated/resumes/kashane-sakhakorn-mid-senior-software-engineer-2026-04-17.md",
    ),
)


def _pick_curated_resume(lead: dict) -> tuple[Path | None, str]:
    """Resolve the curated resume lane for this lead.

    Returns ``(path, warning_code)``:
    - ``(Path, "")`` — lane matched and the source file exists on disk.
    - ``(None, "curated_source_missing")`` — a non-wildcard lane matched
      but its source file is missing. Caller should emit a warning and
      fall back to the template so the user sees the audit trail.
    - ``(None, "")`` — wildcard default with no source on disk, or no
      lane matched at all. Silent fallback to the template.

    Uses `utils.repo_root()` to resolve file paths — same convention as
    `application.py`, `playbooks.py`, and `confirmation.py`.

    Consults the config-driven resume variant registry first
    (`config/resume-variants.json`). The registry only *wins* when its routed
    resume file exists on disk; otherwise this falls through to the legacy
    hardcoded `CURATED_RESUME_LANES` below, preserving prior behavior. The full
    routing decision (including missing-file review flags) is surfaced by the
    `select-resume-variant` CLI, not buried in generation warnings.
    """
    from .resume_registry import pick_registry_resume

    registry_path, _decision = pick_registry_resume(lead)
    if registry_path is not None:
        return registry_path, ""

    title_lc = str(lead.get("title") or "").lower()
    root = repo_root()
    for keywords, rel_path in CURATED_RESUME_LANES:
        is_wildcard = not keywords
        matched = is_wildcard or any(kw in title_lc for kw in keywords)
        if not matched:
            continue
        candidate = root / rel_path
        if candidate.exists():
            return candidate, ""
        # Non-wildcard lane matched but file missing — audit-worthy.
        if not is_wildcard:
            return None, "curated_source_missing"
        # Wildcard: silent fallback.
        return None, ""
    return None, ""


def generation_tokens(text: str) -> list[str]:
    """Like core.tokens() but preserves 2-char terms (AI, ML, Go, UI, CI, CD, QA)."""
    return re.findall(r"[a-z0-9+#.-]{2,}", text.lower())


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity. Returns 0.0 if union is empty."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def select_accomplishments_for_variant(
    highlights: list[dict],
    lead_keywords: set[str],
    style: str,
    limit: int = 6,
) -> list[str]:
    """Score and select accomplishments for a resume variant.

    Score = 0.7 * jaccard(accomplishment_tokens, lead_keywords)
          + 0.3 * phrase_boost
    Returns a flat list of summary strings.
    """
    boost_phrases = VARIANT_BOOST_PHRASES.get(style, [])

    scored: list[tuple[float, str, list[str]]] = []
    for h in highlights:
        summary = h.get("summary", "")
        h_tokens = set(generation_tokens(summary))
        lead_relevance = _jaccard(h_tokens, lead_keywords)

        summary_lower = summary.lower()
        phrase_hits = sum(1 for phrase in boost_phrases if phrase in summary_lower)
        phrase_boost = min(phrase_hits / max(len(boost_phrases), 1), 1.0)

        score = 0.7 * lead_relevance + 0.3 * phrase_boost
        scored.append((score, summary, h.get("source_document_ids", [])))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [summary for _, summary, _ in scored[:limit]]


def select_skills_for_variant(
    skills: list[dict],
    lead_keywords: set[str],
    style: str,
    limit: int = 12,
) -> list[str]:
    """Select skills ordered by relevance to the lead."""
    scored: list[tuple[float, str]] = []
    for s in skills:
        name = s.get("name", "")
        name_tokens = set(generation_tokens(name))
        relevance = _jaccard(name_tokens, lead_keywords)
        scored.append((relevance, name))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored[:limit]]


def _variant_summary(style: str, lead: dict, matched_skills: list[str]) -> str:
    """Generate a professional summary paragraph tailored to the variant style."""
    title = lead.get("title", "engineering")
    company = lead.get("company", "the team")
    top_skills = ", ".join(matched_skills[:4]) or "relevant engineering experience"

    if style == STYLE_TECHNICAL_DEPTH:
        return (
            f"Experienced engineer with deep expertise in {top_skills}. "
            f"Focused on building robust, scalable systems with strong technical foundations. "
            f"Seeking to bring architectural depth to the {title} role at {company}."
        )
    if style == STYLE_IMPACT_FOCUSED:
        return (
            f"Results-driven engineer with proven impact across {top_skills}. "
            f"Track record of delivering measurable business outcomes through technology. "
            f"Eager to drive impact as {title} at {company}."
        )
    # breadth or default
    return (
        f"Versatile engineer with broad experience spanning {top_skills}. "
        f"Comfortable operating across the stack and leading cross-functional initiatives. "
        f"Looking to apply this breadth to the {title} role at {company}."
    )


def render_resume_markdown(
    candidate_profile: dict,
    selected_accomplishments: list[str],
    selected_skills: list[str],
    style: str,
    lead: dict,
) -> str:
    """Render a markdown resume from profile data and selections."""
    contact = candidate_profile.get("contact", {})
    name_parts = []
    emails = contact.get("emails", [])
    phones = contact.get("phones", [])
    links = contact.get("links", [])

    # Candidate name: try to infer from profile or use a default.
    prefs = candidate_profile.get("preferences", {})
    candidate_name = prefs.get("candidate_name", "Candidate")

    contact_line_parts = []
    if emails:
        contact_line_parts.append(emails[0])
    if phones:
        contact_line_parts.append(phones[0])
    if links:
        contact_line_parts.append(links[0])
    contact_line = " | ".join(contact_line_parts)

    summary = _variant_summary(style, lead, selected_skills[:4])

    skills_text = ", ".join(selected_skills)

    # Build accomplishment bullets.
    accomplishment_text = "\n".join(f"- {a}" for a in selected_accomplishments)

    # Education from profile if available.
    education = candidate_profile.get("education", [])
    education_text = ""
    if education:
        edu_lines = []
        for e in education:
            if isinstance(e, dict):
                edu_lines.append(f"- {e.get('degree', '')} — {e.get('institution', '')}")
            else:
                edu_lines.append(f"- {e}")
        education_text = "\n".join(edu_lines)
    else:
        education_text = "- Details available upon request"

    return f"""# {candidate_name}

{contact_line}

## Professional Summary

{summary}

## Technical Skills

{skills_text}

## Professional Experience

{accomplishment_text}

## Education

{education_text}
"""


def generate_resume_variants(
    lead: dict,
    candidate_profile: dict,
    variant_styles: list[str],
    output_dir: Path,
) -> list[dict]:
    """Generate resume variants for a lead, returning content records."""
    ensure_dir(output_dir)
    highlights = candidate_profile.get("experience_highlights", [])
    skills = candidate_profile.get("skills", [])
    documents = candidate_profile.get("documents", [])
    doc_ids = [d["document_id"] for d in documents[:3]]

    lead_keywords = set(generation_tokens(
        f"{lead.get('title', '')} "
        f"{' '.join(lead.get('normalized_requirements', {}).get('keywords', []))} "
        f"{' '.join(lead.get('normalized_requirements', {}).get('required', []))}"
    ))

    results: list[dict] = []
    ts = now_iso()
    ts_compact = ts.replace(":", "").replace("-", "").replace("+", "").replace("T", "T")[:15] or ts

    curated_path, lane_warning = _pick_curated_resume(lead)
    root = repo_root()

    for style in variant_styles:
        selected_acc = select_accomplishments_for_variant(highlights, lead_keywords, style)
        selected_sk = select_skills_for_variant(skills, lead_keywords, style)
        generation_warnings: list[dict] = []
        if curated_path is not None:
            # Use the curated, hand-crafted, ATS-passing resume verbatim.
            md_content = curated_path.read_text(encoding="utf-8")
            provenance = "curated"
        else:
            md_content = render_resume_markdown(candidate_profile, selected_acc, selected_sk, style, lead)
            provenance = "grounded"
            if lane_warning == "curated_source_missing":
                # Audit-visible: a curated lane matched but the source file is
                # missing on disk. Fall back to the template (already done
                # above) but surface the miss so the user can reconcile.
                generation_warnings.append({
                    "code": "curated_source_missing",
                    "severity": "warning",
                    "detail": (
                        "curated resume lane matched but the source file was "
                        "missing on disk; fell back to templated resume"
                    ),
                })

        lead_slug = slugify(f"{lead.get('company', 'unknown')}-{lead.get('title', 'role')}")
        content_id = f"{lead_slug}-{style}-{ts_compact}"

        record: dict = {
            "content_id": content_id,
            "content_type": "resume",
            "variant_style": style,
            "generated_at": ts,
            "lead_id": lead.get("lead_id", ""),
            "job_title": lead.get("title", ""),
            "source_document_ids": doc_ids,
            "selected_accomplishments": selected_acc,
            "selected_skills": selected_sk,
            "output_path": str(output_dir / f"{content_id}.md"),
            "provenance": provenance,
        }
        if curated_path is not None:
            # Store the repo-relative path so records remain portable across
            # machines; absolute paths would pin to this checkout.
            try:
                record["curated_source"] = str(curated_path.relative_to(root))
            except ValueError:
                record["curated_source"] = str(curated_path)
        if generation_warnings:
            record["generation_warnings"] = generation_warnings
        write_json(output_dir / f"{content_id}.json", record)
        (output_dir / f"{content_id}.md").write_text(md_content, encoding="utf-8")
        results.append(record)

    return results


# --- Answer generation ---

ATS_KNOCKOUT_KEYWORDS: dict[str, dict] = {
    "work_authorization": {
        "keywords": ("authorized to work", "legally authorized", "work authorization", "eligible to work"),
        "profile_key": "work_authorization",
        "is_knockout": True,
    },
    "visa_sponsorship": {
        "keywords": ("sponsorship", "visa sponsor", "require sponsorship"),
        "profile_key": "work_authorization",
        "is_knockout": True,
    },
    "salary_expectations": {
        "keywords": ("salary", "compensation", "pay expectation", "expected annual"),
        "profile_key": "minimum_compensation",
        "is_knockout": False,
    },
    "start_date": {
        "keywords": ("start date", "available to start", "earliest start"),
        "profile_key": "search_timeline",
        "is_knockout": False,
    },
    "relocation": {
        "keywords": ("relocat", "willing to move"),
        "profile_key": "preferred_locations",
        "is_knockout": False,
    },
    "remote_preference": {
        "keywords": ("remote", "onsite", "on-site", "hybrid", "work model", "work arrangement"),
        "profile_key": "remote_preference",
        "is_knockout": False,
    },
}


def match_question_to_knockout(question: str, preferences: dict) -> dict | None:
    """Simple keyword-in-lowered-question matching. Returns match info or None."""
    q_lower = question.lower()
    for category, info in ATS_KNOCKOUT_KEYWORDS.items():
        if any(kw in q_lower for kw in info["keywords"]):
            profile_key = info["profile_key"]
            value = preferences.get(profile_key, "")
            if isinstance(value, list):
                value = ", ".join(value) if value else ""
            return {
                "category": category,
                "profile_key": profile_key,
                "value": str(value) if value else "",
                "is_knockout": info["is_knockout"],
                "matched": bool(value),
            }
    return None


def match_question_to_bank(
    question: str,
    question_bank: list[dict],
    threshold: float = 0.3,
) -> list[tuple[dict, float]]:
    """Jaccard token similarity against all bank entries.

    Returns matches above threshold, sorted by score descending.
    """
    q_tokens = set(generation_tokens(question))
    matches: list[tuple[dict, float]] = []
    for entry in question_bank:
        entry_tokens = set(generation_tokens(entry.get("question", "")))
        score = _jaccard(q_tokens, entry_tokens)
        if score >= threshold:
            matches.append((entry, score))
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches


def generate_answer_set(
    lead: dict,
    candidate_profile: dict,
    questions: list[str],
    runtime_policy: dict,
    output_dir: Path,
) -> dict:
    """Generate answers for application questions.

    Returns a generated content record with prepared answers.
    """
    ensure_dir(output_dir)
    preferences = candidate_profile.get("preferences", {})
    question_bank = candidate_profile.get("question_bank", [])
    documents = candidate_profile.get("documents", [])
    doc_ids = [d["document_id"] for d in documents[:3]]

    allow_inferred = runtime_policy.get("allow_inferred_answers", True)
    stop_if_missing = runtime_policy.get("stop_if_required_fact_missing", True)

    answers: list[dict] = []
    blocked = False

    for question in questions:
        # 1. Check knockout categories first.
        knockout = match_question_to_knockout(question, preferences)
        if knockout:
            if knockout["matched"]:
                answers.append({
                    "question": question,
                    "answer": knockout["value"],
                    "provenance": "grounded",
                    "confidence": 0.95,
                    "needs_review": False,
                    "source_document_ids": doc_ids,
                    "category": knockout["category"],
                })
            else:
                if knockout["is_knockout"] and stop_if_missing:
                    blocked = True
                answers.append({
                    "question": question,
                    "answer": "",
                    "provenance": "grounded",
                    "confidence": 0.0,
                    "needs_review": True,
                    "source_document_ids": [],
                    "category": knockout["category"],
                    "missing_fact": True,
                })
            continue

        # 2. Match against question bank.
        bank_matches = match_question_to_bank(question, question_bank)
        if bank_matches:
            best, score = bank_matches[0]
            if score >= 0.5:
                answers.append({
                    "question": question,
                    "answer": best["answer"],
                    "provenance": "grounded",
                    "confidence": score,
                    "needs_review": False,
                    "source_document_ids": best.get("source_document_ids", []),
                })
            elif score >= 0.3:
                answers.append({
                    "question": question,
                    "answer": best["answer"],
                    "provenance": "synthesized",
                    "confidence": score,
                    "needs_review": True,
                    "source_document_ids": best.get("source_document_ids", []),
                })
            continue

        # 3. No match — check inference policy.
        if allow_inferred:
            answers.append({
                "question": question,
                "answer": "",
                "provenance": "weak_inference",
                "confidence": 0.1,
                "needs_review": True,
                "source_document_ids": [],
            })
        else:
            answers.append({
                "question": question,
                "answer": "",
                "provenance": "grounded",
                "confidence": 0.0,
                "needs_review": True,
                "source_document_ids": [],
                "missing_fact": True,
            })

    ts = now_iso()
    lead_slug = slugify(f"{lead.get('company', 'unknown')}-{lead.get('title', 'role')}")
    ts_compact = ts.replace(":", "").replace("-", "").replace("+", "").replace("T", "T")[:15] or ts
    content_id = f"{lead_slug}-answers-{ts_compact}"

    record = {
        "content_id": content_id,
        "content_type": "answer_set",
        "variant_style": "default",
        "generated_at": ts,
        "lead_id": lead.get("lead_id", ""),
        "job_title": lead.get("title", ""),
        "source_document_ids": doc_ids,
        "selected_accomplishments": [],
        "selected_skills": [],
        "output_path": str(output_dir / f"{content_id}.json"),
        "provenance": "synthesized",
        "answers": answers,
        "blocked": blocked,
    }
    write_json(output_dir / f"{content_id}.json", record)
    return record


# --- Cover letter generation ---
#
# Lane-aware composition pipeline. See docs/plans/2026-04-18-001-feat-cover-letter-lanes-plan.md
# for the design contract. Three strength lanes, evidence selection, and pre-write
# guardrails against placeholder/stale-company leakage.

CoverLetterLaneId = Literal["platform_internal_tools", "ai_engineer", "product_minded_engineer"]

COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS: Final = "platform_internal_tools"
COVER_LETTER_LANE_AI_ENGINEER: Final = "ai_engineer"
COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER: Final = "product_minded_engineer"

# Tiebreaker order per plan §3: stable priority when lane scores are within tolerance.
COVER_LETTER_LANE_PRIORITY: Final[tuple[str, ...]] = (
    COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
    COVER_LETTER_LANE_AI_ENGINEER,
    COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER,
)

# Confidence thresholds per plan §2.
COVER_LETTER_MIN_LANE_SCORE: Final = 0.15
COVER_LETTER_MIN_LANE_MARGIN: Final = 0.05
COVER_LETTER_LANE_TIE_TOLERANCE: Final = 0.001

# Stale-name denylist per plan §5. Whole-word boundary + case-insensitive match,
# with an escape hatch when the name equals the target company.
STALE_COMPANY_DENYLIST: Final[frozenset[str]] = frozenset({"SpaceX", "Kadince"})

# Project-note allowlist per plan §"Minimal Slice".
COVER_LETTER_PROJECT_NOTE_ALLOWLIST: Final[frozenset[str]] = frozenset({"job-hunt", "ai-company-os"})

# Company-specific nouns used by the deterministic unsupported-fact matcher (Phase 3).
UNSUPPORTED_COMPANY_NOUNS: Final[tuple[str, ...]] = (
    "mission", "vision", "culture", "customers", "product", "values",
)

# Placeholder pattern matches typical template leakage like [Company], [Role], {Company}.
_PLACEHOLDER_PATTERN: Final = re.compile(r"[\[\{]\s*(?:Company|Role|Team|Hiring Manager|Name)\s*[\]\}]", re.IGNORECASE)

# --- Claims-bank source policy (constrains generated cover-letter prose) ---
#
# Cover letters may only build prose from approved, lane-appropriate claims plus
# safe profile identity and grounded lead/company metadata. Raw intake (resume
# headings, old cover-letter titles, normalized question-bank answers) is NOT a
# safe source: it carries softened-away over-claims and document headings that
# leak verbatim. The denylist + safety filters below are the deterministic
# backstop; sourcing from approved claims is the primary defense.

# Sign-off marker emitted when no reviewed candidate name is available. Never
# fall back to a generic "Candidate" signature — surface the gap for review.
NEEDS_USER_REVIEW_NAME: Final = "NEEDS_USER_REVIEW_NAME"

# Map each cover-letter lane to the claims-bank `allowed_lanes` it may draw from.
# generalist_swe is included everywhere as a safe, broadly-applicable fallback.
COVER_LETTER_LANE_TO_CLAIMS_LANES: Final[dict[str, tuple[str, ...]]] = {
    COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS: ("platform_backend", "generalist_swe"),
    COVER_LETTER_LANE_AI_ENGINEER: ("ai_engineer", "generalist_swe"),
    COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER: ("fullstack_product", "generalist_swe"),
}

# Softened-away / unsupported phrases that must never appear in generated prose,
# regardless of source. These are the specific fragments the claims bank softened
# out (absolute integrity/loss wording, the unsourced "up to 50%" metric) plus
# the unsupported personal stack (AI Notecards / RN / RevenueCat). Company-fact
# nouns such as "kubernetes" are deliberately excluded — a target company may
# legitimately use them, and we only source candidate prose from approved claims.
SOFTENED_CLAIM_DENYLIST: Final[tuple[str, ...]] = (
    "100% data integrity",
    "zero data loss",
    "no data loss",
    "without data loss",
    "up to 50%",
    "ai notecards",
    "react native",
    "revenuecat",
)

# Markers that flag raw intake / template text leaking into prose.
_RAW_HEADING_PATTERN: Final = re.compile(r"^\s*#{1,6}\s", re.MULTILINE)
_TEMPLATE_BRACKET_PATTERN: Final = re.compile(
    r"\[[^\]]*\b(?:fill in|fill-in|your|tbd|todo|placeholder|optional)\b[^\]]*\]",
    re.IGNORECASE,
)
_RAW_COVER_LETTER_HEADING: Final = re.compile(r"cover letter\s*:", re.IGNORECASE)
# Scale/revenue dollar figures (e.g. "$10M+", "$10 million", "$500K") read as
# inflated self-credit in a cover letter even when an approved claim frames them
# as system context. Keep them out of generated cover-letter prose; resumes,
# which use a separate renderer, are unaffected.
_REVENUE_FIGURE_PATTERN: Final = re.compile(
    r"\$\s?\d[\d,.]*\+?\s?(?:k|m|mm|b|million|billion)\b", re.IGNORECASE
)


def _cover_letter_denylist(claims_bank: dict | None) -> tuple[str, ...]:
    """Return the lowercased phrase denylist for guardrails.

    Always includes the built-in softened/unsupported fragments. The claims
    bank's `never_claim` entries are full sentences that rarely substring-match
    generated prose, so they are not folded in here — they are enforced by
    sourcing prose only from approved claims, not by phrase matching.
    """
    return tuple(dict.fromkeys(p.lower() for p in SOFTENED_CLAIM_DENYLIST))


def _unsafe_prose_reason(text: str, denylist: tuple[str, ...]) -> str | None:
    """Return a reason string if `text` is unsafe to use as generated prose, else None.

    Unsafe = empty, a raw markdown heading, a raw cover-letter heading, a template
    placeholder bracket, or contains a denylisted softened/unsupported phrase.
    """
    if not text or not text.strip():
        return "empty"
    if _RAW_HEADING_PATTERN.search(text):
        return "raw_markdown_heading"
    if _RAW_COVER_LETTER_HEADING.search(text):
        return "raw_cover_letter_heading"
    if _TEMPLATE_BRACKET_PATTERN.search(text):
        return "template_placeholder"
    if _REVENUE_FIGURE_PATTERN.search(text):
        return "revenue_figure"
    low = text.lower()
    for phrase in denylist:
        if phrase in low:
            return f"denylisted_phrase:{phrase}"
    return None


def approved_claims_as_highlights(
    claims_bank: dict | None, cover_letter_lane_id: str
) -> list[dict]:
    """Adapt approved, lane-appropriate claims into highlight-shaped dicts.

    Only `review_status == "approved"` claims are returned (needs_user_review,
    draft, and rejected claims are excluded), and only those whose allowed_lanes
    intersect the cover-letter lane's mapped claims lanes. The returned shape
    matches `experience_highlights` so it flows through the existing scorer.
    """
    if not claims_bank:
        return []
    target_lanes = set(COVER_LETTER_LANE_TO_CLAIMS_LANES.get(cover_letter_lane_id, ()))
    highlights: list[dict] = []
    for claim in claims_bank.get("claims", []) or []:
        if claim.get("review_status") != "approved":
            continue
        allowed = set(claim.get("allowed_lanes", []) or [])
        if target_lanes and not (allowed & target_lanes):
            continue
        text = (claim.get("claim_text") or "").strip()
        if not text:
            continue
        highlights.append({
            "summary": text,
            "source_document_ids": [f"claim:{claim.get('claim_id', '')}"],
            # Technologies feed lane-relevance scoring so backend/platform claims
            # outrank frontend ones for backend leads (and vice-versa).
            "technologies": [str(t).lower() for t in (claim.get("technologies") or [])],
        })
    return highlights


def _resolve_candidate_name(candidate_profile: dict) -> str:
    """Resolve a reviewed candidate name, or the review marker — never "Candidate".

    Order: preferences.candidate_name, then contact.name, then the review marker.
    """
    prefs = candidate_profile.get("preferences", {}) or {}
    name = (prefs.get("candidate_name") or "").strip()
    if name and name.lower() != "candidate":
        return name
    contact = candidate_profile.get("contact", {}) or {}
    cname = (contact.get("name") or "").strip()
    if cname and cname.lower() != "candidate":
        return cname
    return NEEDS_USER_REVIEW_NAME


def load_cover_letter_claims_bank(root: Path | None = None) -> dict | None:
    """Load the private claims bank for cover-letter sourcing, else the example.

    Disk I/O lives here (not in the pure generator) so tests can pass an explicit
    claims_bank fixture and stay deterministic regardless of local private files.
    """
    from .utils import read_json, repo_root

    base = root or repo_root()
    for rel in ("profile/claims/claims-bank.json", "profile/claims/claims-bank.example.json"):
        p = base / rel
        if p.exists():
            try:
                return read_json(p)
            except (OSError, ValueError):
                continue
    return None


@dataclass(frozen=True)
class CoverLetterLaneSpec:
    """Static configuration for a cover-letter lane.

    - preferred_keywords: single-word tokens used in scoring jaccard.
    - preferred_phrases: multi-word phrases used in scoring phrase_boost.
    - project_note_doc_ids: allowlisted project-note document ids this lane prefers.
    - voice_* fields: lane-specific prose fragments for the renderer.
    """

    lane_id: str
    preferred_keywords: tuple[str, ...]
    preferred_phrases: tuple[str, ...]
    project_note_doc_ids: tuple[str, ...]
    opening_emphasis: str
    proof_framing: str
    closing_value_prop: str


COVER_LETTER_LANE_SPECS: Final[dict[str, CoverLetterLaneSpec]] = {
    COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS: CoverLetterLaneSpec(
        lane_id=COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
        preferred_keywords=(
            "platform", "internal", "tools", "tooling", "backend", "infrastructure",
            "migration", "data", "integration", "api", "reliability",
        ),
        preferred_phrases=(
            "internal tools", "platform engineering", "legacy modernization",
            "data migration", "internal api", "operational tooling",
            "cross-functional", "system design",
        ),
        project_note_doc_ids=("job-hunt",),
        opening_emphasis=(
            "roles where backend engineering and platform work have a direct impact "
            "on reliability, maintainability, and team effectiveness"
        ),
        proof_framing="a practical engineering mindset focused on clear systems and strong data foundations",
        closing_value_prop="backend engineering, internal systems, and legacy modernization",
    ),
    COVER_LETTER_LANE_AI_ENGINEER: CoverLetterLaneSpec(
        lane_id=COVER_LETTER_LANE_AI_ENGINEER,
        preferred_keywords=(
            "ai", "ml", "llm", "agent", "agents", "rag", "retrieval", "embeddings",
            "model", "prompt", "automation", "workflow", "pipeline",
        ),
        preferred_phrases=(
            "ai engineering", "ai systems", "human-in-the-loop", "agent orchestration",
            "retrieval augmented", "typed workflows", "safe automation",
            "internal automation", "machine learning",
        ),
        project_note_doc_ids=("ai-company-os", "job-hunt"),
        opening_emphasis=(
            "AI engineering roles where strong software fundamentals, human-in-the-loop "
            "controls, and thoughtful system design matter as much as model choice"
        ),
        proof_framing="production engineering judgment paired with genuine curiosity about where AI systems become useful",
        closing_value_prop="software engineering, internal automation, and AI systems design",
    ),
    COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER: CoverLetterLaneSpec(
        lane_id=COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER,
        preferred_keywords=(
            "product", "user", "workflow", "impact", "customer", "experience",
            "operations", "ops", "internal", "tools",
        ),
        preferred_phrases=(
            "user empathy", "product minded", "internal tools", "workflow improvements",
            "practical impact", "cross-functional", "operational pain points",
        ),
        project_note_doc_ids=("job-hunt",),
        opening_emphasis=(
            "software engineering as a chance to build practical tools that make "
            "people's work easier, clearer, and more effective"
        ),
        proof_framing="a combination of user empathy and technical depth",
        closing_value_prop="internal tools, workflow improvements, and practical software with real impact",
    ),
}


# Question-bank prompt filters — v1 prefers generic prompts and filters company-specific ones.
_GENERIC_QUESTION_MARKERS: Final[tuple[str, ...]] = (
    "why", "tell me about", "describe", "what", "how do you", "strongest",
    "proud", "work style", "approach",
)
_COMPANY_SPECIFIC_QUESTION_MARKERS: Final[tuple[str, ...]] = (
    "this company", "our company", "our product", "our mission", "our values",
    "our culture", "this role at", "why us",
)


# --- Detection helpers (imported by ats_check.py for Phase 3 backstop) ---

def find_unresolved_placeholders(text: str) -> list[str]:
    """Return the list of unresolved placeholder tokens like [Company], {Role}.

    Pure function, no I/O. Used by generation-time guardrails and ATS hard-error check.
    """
    return [m.group(0) for m in _PLACEHOLDER_PATTERN.finditer(text)]


def find_stale_company_mentions(
    text: str,
    target_company: str,
    denylist: frozenset[str] = STALE_COMPANY_DENYLIST,
) -> list[str]:
    """Return denylisted company names that appear in text.

    Escape hatch: a denylisted name that equals the target_company (case-insensitive)
    is not flagged, so a legitimate target of "SpaceX" itself still works. Matches
    use whole-word boundaries to avoid substring false positives.
    """
    target_lower = (target_company or "").strip().lower()
    hits: list[str] = []
    for name in denylist:
        if name.lower() == target_lower:
            continue
        pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
        if pattern.search(text):
            hits.append(name)
    return hits


# --- Lane selection ---

def _lead_keyword_tokens(lead: dict) -> set[str]:
    """Combined tokens used for lane scoring. Mirrors resume variant usage."""
    normalized = lead.get("normalized_requirements", {}) or {}
    text = " ".join([
        lead.get("title", "") or "",
        " ".join(normalized.get("keywords", []) or []),
        " ".join(normalized.get("required", []) or []),
    ])
    return set(generation_tokens(text))


def choose_cover_letter_lane(
    lead: dict,
    candidate_profile: dict,
    explicit_lane: str | None = None,
) -> tuple[str, str, str, list[dict]]:
    """Score lanes and return (lane_id, lane_source, rationale, warnings).

    warnings is a list of {code, severity, detail} records to attach to the content
    record. lane_source is "explicit" or "auto".

    Raises ValueError with code=invalid_lane_id for unknown explicit lanes.
    """
    warnings: list[dict] = []

    # Score all lanes regardless of explicit mode (cheap + useful for rationale).
    lead_keywords = _lead_keyword_tokens(lead)
    scores = _score_all_lanes(lead_keywords)

    if explicit_lane is not None:
        if explicit_lane not in COVER_LETTER_LANE_SPECS:
            raise _CoverLetterError(
                "invalid_lane_id",
                f"Unknown cover-letter lane: {explicit_lane!r}. "
                f"Expected one of {sorted(COVER_LETTER_LANE_SPECS)}.",
            )
        auto_winner = _pick_auto_winner(scores)
        if auto_winner != explicit_lane:
            warnings.append({
                "code": "lane_low_confidence",
                "severity": "warning",
                "detail": (
                    f"explicit lane {explicit_lane!r} differs from auto pick {auto_winner!r} "
                    f"(scores: {_format_scores(scores)})"
                ),
            })
        rationale = f"explicit override; auto pick would be {auto_winner} ({_format_scores(scores)})"
        return explicit_lane, "explicit", rationale, warnings

    winner = _pick_auto_winner(scores)
    winner_score = scores[winner]
    sorted_scores = sorted(scores.values(), reverse=True)
    runner_up = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
    margin = winner_score - runner_up

    # Confidence is about *ambiguity*, not absolute magnitude. A job description's
    # keyword set is often diluted by generic prose, so jaccard scores run small
    # even for a clear-cut lane. Flag only when there is no domain signal at all,
    # or when a real runner-up sits within the margin (a genuinely close call).
    # A decisive winner that simply scores low is left unflagged.
    ambiguous = winner_score <= 0.0 or (runner_up > 0.0 and margin < COVER_LETTER_MIN_LANE_MARGIN)
    if ambiguous:
        reason = "no domain signal" if winner_score <= 0.0 else "close runner-up"
        warnings.append({
            "code": "lane_low_confidence",
            "severity": "warning",
            "detail": (
                f"winner {winner!r} score={winner_score:.3f} runner_up={runner_up:.3f} "
                f"margin={margin:.3f} ({reason})"
            ),
        })

    rationale = f"auto-selected by lane scoring ({_format_scores(scores)})"
    return winner, "auto", rationale, warnings


def _score_all_lanes(lead_keywords: set[str]) -> dict[str, float]:
    """Compute the lane_score = 0.7 * jaccard + 0.3 * phrase_boost for each lane.

    Mirrors select_accomplishments_for_variant. Phrase boost checks a joined
    keyword-bag string for phrase presence.
    """
    keyword_text = " ".join(sorted(lead_keywords))
    scores: dict[str, float] = {}
    for lane_id, spec in COVER_LETTER_LANE_SPECS.items():
        lane_tokens = set(spec.preferred_keywords)
        jaccard = _jaccard(lane_tokens, lead_keywords)
        phrase_hits = sum(1 for phrase in spec.preferred_phrases if phrase.lower() in keyword_text.lower())
        phrase_boost = min(phrase_hits / max(len(spec.preferred_phrases), 1), 1.0)
        scores[lane_id] = 0.7 * jaccard + 0.3 * phrase_boost
    return scores


def _pick_auto_winner(scores: dict[str, float]) -> str:
    """Return the winning lane id, resolving ties via COVER_LETTER_LANE_PRIORITY."""
    max_score = max(scores.values())
    # Candidates tied within tolerance compete via priority order.
    candidates = [
        lane_id for lane_id, s in scores.items()
        if abs(s - max_score) <= COVER_LETTER_LANE_TIE_TOLERANCE
    ]
    for lane_id in COVER_LETTER_LANE_PRIORITY:
        if lane_id in candidates:
            return lane_id
    # Should not happen — every lane is in COVER_LETTER_LANE_PRIORITY.
    return candidates[0]


def _format_scores(scores: dict[str, float]) -> str:
    items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ", ".join(f"{lane}={score:.3f}" for lane, score in items)


# --- Evidence selection ---

def select_cover_letter_evidence(
    lane_spec: CoverLetterLaneSpec,
    lead: dict,
    candidate_profile: dict,
    company_research: dict | None,
    explicit_mode: bool = False,
    claims_bank: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Return (evidence, warnings).

    evidence is a dict with keys:
      - top_skills: list[str]
      - top_accomplishments: list[str] (summary strings)
      - accomplishment_source_docs: list[str] (source_document_ids actually used)
      - question_bank_entries: list[dict] ({question, answer})
      - project_note_doc_ids: list[str] (allowlisted ids actually present in profile)
      - company_facts_used: list[{source, field, value}]

    Raises _CoverLetterError("zero_grounded_evidence") when no evidence remains
    and we cannot safely render a letter.

    The plan's minimum evidence bar (plan §4):
      - ≥1 accomplishment with nonzero lead-keyword overlap OR ≥1 allowlisted project-note
      - AND ≥2 matched skills OR ≥1 matched requirement keyword
    """
    warnings: list[dict] = []
    lead_keywords = _lead_keyword_tokens(lead)
    denylist = _cover_letter_denylist(claims_bank)
    claims_mode = claims_bank is not None

    # Skills: filter to those with nonzero overlap vs lead, ordered by jaccard.
    skills = candidate_profile.get("skills", []) or []
    top_skills = select_skills_for_variant(skills, lead_keywords, STYLE_IMPACT_FOCUSED, limit=6)
    matched_skill_count = sum(1 for s in top_skills if set(generation_tokens(s)) & lead_keywords)

    # Accomplishments source: approved, lane-appropriate claims when a claims bank
    # is supplied; otherwise the (filtered) normalized experience highlights.
    if claims_mode:
        raw_highlights = approved_claims_as_highlights(claims_bank, lane_spec.lane_id)
        if not raw_highlights:
            warnings.append({
                "code": "no_approved_claims",
                "severity": "warning",
                "detail": (
                    f"no approved claims available for lane {lane_spec.lane_id!r}; "
                    f"letter falls back to conservative, non-specific prose"
                ),
            })
    else:
        raw_highlights = candidate_profile.get("experience_highlights", []) or []

    # Safety filter: never let raw headings, template placeholders, or
    # softened/unsupported phrases ride into generated prose.
    highlights: list[dict] = []
    for h in raw_highlights:
        reason = _unsafe_prose_reason(h.get("summary", ""), denylist)
        if reason:
            warnings.append({
                "code": "unsafe_prose_filtered",
                "severity": "warning",
                "detail": f"dropped accomplishment ({reason})",
            })
            continue
        highlights.append(h)

    scored_accomplishments = _score_accomplishments_for_lane(highlights, lead_keywords, lane_spec)
    selected = [h for _, h in scored_accomplishments[:3]]
    # Claims-mode: approved claims are all safe to surface. If none cleared the
    # keyword-overlap scorer, still ground the letter in approved claims (bank
    # order) rather than falling through to an empty proof paragraph.
    if claims_mode and not selected and highlights:
        selected = highlights[:3]
    top_accomplishments = [h.get("summary", "") for h in selected if h.get("summary")]
    accomplishment_source_docs = [
        doc_id for h in selected for doc_id in (h.get("source_document_ids") or [])
    ]

    # Question bank: raw normalized answers are intake, not approved claims, so
    # they are excluded entirely in claims-mode. In no-bank mode (sanitized
    # fixtures), keep generic prompts but still filter unsafe answers.
    if claims_mode:
        question_bank_entries: list[dict] = []
    else:
        candidate_qb = _filter_question_bank(candidate_profile.get("question_bank", []) or [])
        safe_qb: list[dict] = []
        for e in candidate_qb:
            reason = _unsafe_prose_reason(e.get("answer", ""), denylist)
            if reason:
                warnings.append({
                    "code": "unsafe_prose_filtered",
                    "severity": "warning",
                    "detail": f"dropped question-bank answer ({reason})",
                })
                continue
            safe_qb.append(e)
        question_bank_entries = safe_qb[:2]

    # Project notes: intersect the lane's preferred doc ids with the profile's documents.
    profile_doc_ids = {d.get("document_id") for d in candidate_profile.get("documents", []) or []}
    lane_note_ids = [
        doc_id for doc_id in lane_spec.project_note_doc_ids
        if doc_id in profile_doc_ids
    ]

    # Requirement keywords matched in candidate profile — we're asking
    # "how many of the lead's required skills does the candidate actually have?",
    # not the tautological "are required items in lead_keywords?".
    normalized = lead.get("normalized_requirements", {}) or {}
    required_set = set(normalized.get("required", []) or [])
    candidate_tokens: set[str] = set()
    for s in skills:
        candidate_tokens.update(generation_tokens(s.get("name", "")))
    for h in highlights:
        candidate_tokens.update(generation_tokens(h.get("summary", "")))
    matched_requirements = required_set & candidate_tokens

    # Company facts: only what's grounded and readable.
    company_facts_used = _collect_company_facts(lead, company_research)
    if company_research is not None:
        research_name = (company_research.get("company_name") or "").strip()
        if research_name and research_name.lower() != (lead.get("company") or "").strip().lower():
            warnings.append({
                "code": "lane_low_confidence",
                "severity": "warning",
                "detail": (
                    f"company_research.company_name={research_name!r} differs from "
                    f"lead.company={(lead.get('company') or '')!r}; dropping research facts"
                ),
            })
            company_facts_used = []

    # --- Evidence bar evaluation ---
    proof_ok = bool(top_accomplishments) or bool(lane_note_ids)
    match_ok = matched_skill_count >= 2 or len(matched_requirements) >= 1

    if not proof_ok or not match_ok:
        # Soft-ground: some evidence missing. Emit warning; hard-fail only on total absence.
        if explicit_mode:
            warnings.append({
                "code": "weak_lane_evidence",
                "severity": "warning",
                "detail": (
                    f"lane {lane_spec.lane_id!r} evidence below bar "
                    f"(proof={proof_ok}, match={match_ok}); explicit override honored"
                ),
            })
        if not proof_ok and not match_ok and not top_skills:
            # Total absence — caller decides whether to hard-fail or fall back.
            raise _CoverLetterError(
                "zero_grounded_evidence",
                f"No grounded evidence available for lane {lane_spec.lane_id!r}: "
                f"no accomplishments, no allowlisted project notes, no matched skills, "
                f"and no matched requirements.",
            )
        # Partial evidence for explicit mode is acceptable (warning already recorded).
        # Auto mode: fall back handled at orchestrator level (select_cover_letter_evidence
        # is called with a specific lane_spec — the orchestrator retries with the next lane).

    evidence = {
        "top_skills": top_skills,
        "top_accomplishments": top_accomplishments,
        "accomplishment_source_docs": accomplishment_source_docs,
        "question_bank_entries": question_bank_entries,
        "project_note_doc_ids": lane_note_ids,
        "company_facts_used": company_facts_used,
        "matched_skill_count": matched_skill_count,
        "matched_requirement_count": len(matched_requirements),
    }
    return evidence, warnings


def _score_accomplishments_for_lane(
    highlights: list[dict],
    lead_keywords: set[str],
    lane_spec: CoverLetterLaneSpec,
) -> list[tuple[float, dict]]:
    """Score highlights against lead keywords, lane domain, and lane phrases.

    Three signals, sorted descending:
      - lead_relevance: overlap with the (cleaned) lead keywords.
      - lane_overlap: overlap with the lane's preferred domain keywords. This is
        what makes a backend lead prefer migration/API/data-integrity claims over
        a frontend ops-UI claim — and, symmetrically, a product/frontend lane
        prefer the UI claim. It is general, not role-hardcoded.
      - phrase_boost: lane-specific multi-word phrase presence.

    A claim's `technologies` (when present) join its summary tokens so domain
    signal is captured even when the prose does not spell the stack out.
    """
    lane_tokens = {kw.lower() for kw in lane_spec.preferred_keywords}
    scored: list[tuple[float, dict]] = []
    for h in highlights:
        summary = h.get("summary", "")
        h_tokens = set(generation_tokens(summary))
        h_tokens |= {str(t).lower() for t in (h.get("technologies") or [])}
        lead_relevance = _jaccard(h_tokens, lead_keywords)
        lane_overlap = _jaccard(h_tokens, lane_tokens)

        summary_lower = summary.lower()
        phrase_hits = sum(1 for phrase in lane_spec.preferred_phrases if phrase.lower() in summary_lower)
        phrase_boost = min(phrase_hits / max(len(lane_spec.preferred_phrases), 1), 1.0)

        score = 0.5 * lead_relevance + 0.3 * lane_overlap + 0.2 * phrase_boost
        if score > 0:
            scored.append((score, h))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _filter_question_bank(entries: list[dict]) -> list[dict]:
    """Keep entries with generic prompts (role interest, project highlights, work style, impact).

    Drop entries whose question text references a specific company or prior-application prompt.
    """
    kept: list[dict] = []
    for e in entries:
        question = (e.get("question") or "").lower().strip()
        if not question:
            continue
        if any(marker in question for marker in _COMPANY_SPECIFIC_QUESTION_MARKERS):
            continue
        if any(question.startswith(marker) or marker in question for marker in _GENERIC_QUESTION_MARKERS):
            kept.append(e)
    return kept


def _collect_company_facts(lead: dict, company_research: dict | None) -> list[dict]:
    """Collect grounded company facts ready for alignment paragraph reuse.

    Sources only from lead and company_research. Returns [] when no research is
    provided (caller distinguishes "not provided" from "provided but empty" via the
    record's presence/absence of the field, handled at write time).
    """
    facts: list[dict] = []
    if not company_research:
        return facts
    for field in ("industry", "tech_stack", "remote_policy", "notes"):
        value = company_research.get(field)
        if not value:
            continue
        if isinstance(value, list):
            joined = ", ".join(str(v) for v in value if v)
            if joined:
                facts.append({"source": "company_research", "field": field, "value": joined})
        elif isinstance(value, str) and value.strip():
            facts.append({"source": "company_research", "field": field, "value": value.strip()})
    return facts


# --- Rendering ---

def render_cover_letter_markdown(
    lane_spec: CoverLetterLaneSpec,
    lead: dict,
    candidate_name: str,
    evidence: dict,
    remote_preference: str,
) -> str:
    """Render the final cover letter markdown from a lane spec + selected evidence.

    Combines section planning and rendering per plan §"New Internal Shape" — split
    only when a second renderer materializes.
    """
    title = lead.get("title") or "the role"
    company = lead.get("company") or "your company"
    top_skills: list[str] = evidence["top_skills"]
    top_accomplishments: list[str] = evidence["top_accomplishments"]
    company_facts: list[dict] = evidence["company_facts_used"]
    question_bank_entries: list[dict] = evidence["question_bank_entries"]

    # Opening: role + company + lane-specific emphasis.
    opening = (
        f"I'm excited to apply for the {title} position at {company}. "
        f"I'm most interested in {lane_spec.opening_emphasis}."
    )

    # Proof paragraph: top accomplishment + skills + lane framing. Strip any
    # trailing sentence punctuation so the template's own "." doesn't double up.
    if top_accomplishments:
        accomplishment_text = top_accomplishments[0].rstrip(" .")
    else:
        accomplishment_text = "shipping production systems end-to-end"
    skill_phrase = ", ".join(top_skills[:4]) if top_skills else "relevant engineering domains"
    proof = (
        f"A representative example: {accomplishment_text}. "
        f"My strongest areas include {skill_phrase}, "
        f"which I bring with {lane_spec.proof_framing}."
    )

    # Alignment paragraph: sourced company facts OR role-specific fallback.
    if company_facts:
        industry = next((f["value"] for f in company_facts if f["field"] == "industry"), "")
        tech = next((f["value"] for f in company_facts if f["field"] == "tech_stack"), "")
        fragments = [f"What draws me to {company}"]
        if industry:
            fragments.append(f" in the {industry} space")
        if tech:
            fragments.append(f" is the opportunity to work with {tech}")
        fragments.append(". ")
        fragments.append(
            f"This aligns well with my background and how I want to grow as an engineer."
        )
        alignment = "".join(fragments)
    else:
        # Role-specific fallback: never invent company facts.
        alignment = (
            f"This {title} role stands out because it maps directly to where I'm strongest: "
            f"{lane_spec.closing_value_prop}. I'd rather talk concretely about the work itself "
            f"than speculate about the org, and I'd welcome the chance to learn more in conversation."
        )

    # Optional question-bank reinforcement: drop in at most one candidate-authored paragraph
    # if the generic prompt filter produced something usable.
    qb_fragment = ""
    if question_bank_entries:
        first = question_bank_entries[0]
        answer = (first.get("answer") or "").strip()
        if answer:
            qb_fragment = f"\n\n{answer}\n"

    # Closing: lane value prop + polite sign-off.
    closing = (
        f"I'd welcome the chance to discuss how my background in {lane_spec.closing_value_prop} "
        f"could contribute to {company}. I'm available for {remote_preference} work. "
        f"Thank you for your consideration."
    )

    return (
        f"Dear Hiring Manager,\n\n"
        f"{opening}\n\n"
        f"{proof}\n\n"
        f"{alignment}"
        f"{qb_fragment}\n\n"
        f"{closing}\n\n"
        f"Sincerely,\n{candidate_name}\n"
    )


# --- Orchestrator ---

class _CoverLetterError(ValueError):
    """Hard-failure signal from cover-letter generation carrying a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def generate_cover_letter(
    lead: dict,
    candidate_profile: dict,
    company_research: dict | None,
    output_dir: Path,
    lane: str | None = None,
    claims_bank: dict | None = None,
) -> dict:
    """Generate a lane-aware cover letter assembled from grounded profile data.

    `lane` accepts "auto" or any `CoverLetterLaneId`. `None` is treated as "auto".
    Hard-failure conditions (raise ValueError with `.code` attached):
      - invalid_lane_id: explicit lane is not one of the three known ids
      - missing_lead_field: lead lacks title or company
      - zero_grounded_evidence: no lane has enough evidence
      - unresolved_placeholder: rendered text contains [Company]/[Role]
      - wrong_company_name: rendered text mentions a denylisted non-target company
      - never_claim_violation: rendered text contains a softened/unsupported phrase
      - raw_intake_leak: rendered text leaked a raw cover-letter/document heading

    When `claims_bank` is provided, proof prose is sourced only from approved,
    lane-appropriate claims; raw question-bank answers are excluded. A missing
    reviewed name is signed as NEEDS_USER_REVIEW_NAME (never "Candidate").
    """
    ensure_dir(output_dir)

    lead_title = (lead.get("title") or "").strip()
    lead_company = (lead.get("company") or "").strip()
    if not lead_title or not lead_company:
        raise _CoverLetterError(
            "missing_lead_field",
            f"lead must have non-empty 'title' and 'company' (got title={lead_title!r}, "
            f"company={lead_company!r}).",
        )

    preferences = candidate_profile.get("preferences", {}) or {}
    documents = candidate_profile.get("documents", []) or []
    candidate_name = _resolve_candidate_name(candidate_profile)
    remote_preference = preferences.get("remote_preference", "flexible")

    explicit_lane: str | None = None
    if lane is not None and lane != "auto":
        explicit_lane = lane

    # 1. Choose lane.
    lane_id, lane_source, lane_rationale, generation_warnings = choose_cover_letter_lane(
        lead, candidate_profile, explicit_lane=explicit_lane,
    )
    lane_spec = COVER_LETTER_LANE_SPECS[lane_id]

    if candidate_name == NEEDS_USER_REVIEW_NAME:
        generation_warnings.append({
            "code": "name_needs_review",
            "severity": "warning",
            "detail": (
                "no reviewed candidate name in preferences.candidate_name or contact.name; "
                "signature marked NEEDS_USER_REVIEW_NAME"
            ),
        })

    # 2. Select evidence. Auto mode falls back to next-priority lane on zero-evidence.
    evidence, evidence_warnings = _select_evidence_with_fallback(
        lane_spec, lead, candidate_profile, company_research,
        explicit_mode=(lane_source == "explicit"), claims_bank=claims_bank,
    )
    # _select_evidence_with_fallback may have switched lanes in auto mode; read back.
    lane_id = evidence.pop("_resolved_lane_id", lane_id)
    lane_spec = COVER_LETTER_LANE_SPECS[lane_id]
    generation_warnings.extend(evidence_warnings)

    # 2b. Filter stale-company mentions from free-form candidate inputs that feed rendering.
    # We do a pre-check on selected accomplishment summaries and question-bank answers;
    # the post-render check is the belt-and-braces backstop.
    generation_warnings.extend(
        _pre_filter_stale_mentions(evidence, lead_company)
    )

    # 3. Render.
    md_content = render_cover_letter_markdown(
        lane_spec, lead, candidate_name, evidence, remote_preference,
    )

    # 4. Pre-write guardrails (hard failures).
    placeholders = find_unresolved_placeholders(md_content)
    if placeholders:
        raise _CoverLetterError(
            "unresolved_placeholder",
            f"Rendered cover letter contains unresolved placeholders: {placeholders}",
        )
    stale_hits = find_stale_company_mentions(md_content, lead_company)
    if stale_hits:
        raise _CoverLetterError(
            "wrong_company_name",
            f"Rendered cover letter leaked non-target company name(s) {stale_hits} "
            f"for target {lead_company!r}.",
        )
    # Softened/unsupported phrase backstop: source restriction should keep these
    # out, but a denylisted fragment in the rendered letter is a hard failure.
    denylist = _cover_letter_denylist(claims_bank)
    banned_hits = sorted({p for p in denylist if p in md_content.lower()})
    if banned_hits:
        raise _CoverLetterError(
            "never_claim_violation",
            f"Rendered cover letter contains disallowed phrase(s): {banned_hits}",
        )
    if _RAW_COVER_LETTER_HEADING.search(md_content):
        raise _CoverLetterError(
            "raw_intake_leak",
            "Rendered cover letter leaked a raw cover-letter heading.",
        )

    # 5. Assemble record.
    ts = now_iso()
    lead_slug = slugify(f"{lead_company}-{lead_title}")
    ts_compact = ts.replace(":", "").replace("-", "").replace("+", "").replace("T", "T")[:15] or ts
    content_id = f"{lead_slug}-cover-letter-{ts_compact}"

    # source_document_ids reflects ACTUAL evidence used, not "first three docs" anymore.
    source_doc_ids: list[str] = []
    seen: set[str] = set()
    for doc_id in evidence["accomplishment_source_docs"]:
        if doc_id and doc_id not in seen:
            source_doc_ids.append(doc_id)
            seen.add(doc_id)
    for doc_id in evidence["project_note_doc_ids"]:
        # Map the lane doc_id label to the actual profile document_id where possible.
        match = next(
            (d.get("document_id") for d in documents if d.get("document_id") == doc_id),
            None,
        )
        resolved = match or doc_id
        if resolved not in seen:
            source_doc_ids.append(resolved)
            seen.add(resolved)

    record: dict = {
        "content_id": content_id,
        "content_type": "cover_letter",
        "variant_style": lane_id,  # mirror lane id per plan compatibility rule
        "generated_at": ts,
        "lead_id": lead.get("lead_id", ""),
        "job_title": lead_title,
        "source_document_ids": source_doc_ids,
        "selected_accomplishments": evidence["top_accomplishments"][:2],
        "selected_skills": evidence["top_skills"][:4],
        "output_path": str(output_dir / f"{content_id}.md"),
        "provenance": "synthesized",
        # Lane metadata (optional schema, tolerant consumer already shipped):
        "lane_id": lane_id,
        "lane_source": lane_source,
        "lane_rationale": lane_rationale,
        "selected_question_bank_questions": [
            e.get("question", "") for e in evidence["question_bank_entries"]
        ],
    }
    # Only emit company_facts_used when research was provided (absence vs empty is signal).
    if company_research is not None:
        record["company_facts_used"] = evidence["company_facts_used"]
    if generation_warnings:
        record["generation_warnings"] = generation_warnings

    write_json(output_dir / f"{content_id}.json", record)
    (output_dir / f"{content_id}.md").write_text(md_content, encoding="utf-8")
    return record


def _select_evidence_with_fallback(
    initial_lane_spec: CoverLetterLaneSpec,
    lead: dict,
    candidate_profile: dict,
    company_research: dict | None,
    explicit_mode: bool,
    claims_bank: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Select evidence for the chosen lane; in auto mode, fall back to next priority
    lane on zero evidence. In explicit mode, honor the lane and surface warnings."""
    accumulated_warnings: list[dict] = []
    attempt_order: list[str]
    if explicit_mode:
        attempt_order = [initial_lane_spec.lane_id]
    else:
        # Start with the initial lane, then try others in priority order.
        attempt_order = [initial_lane_spec.lane_id] + [
            lane for lane in COVER_LETTER_LANE_PRIORITY if lane != initial_lane_spec.lane_id
        ]

    last_error: _CoverLetterError | None = None
    for attempt_idx, lane_id in enumerate(attempt_order):
        spec = COVER_LETTER_LANE_SPECS[lane_id]
        try:
            evidence, warnings = select_cover_letter_evidence(
                spec, lead, candidate_profile, company_research,
                explicit_mode=explicit_mode, claims_bank=claims_bank,
            )
        except _CoverLetterError as exc:
            last_error = exc
            if explicit_mode:
                raise
            continue  # try next priority lane
        if attempt_idx > 0:
            accumulated_warnings.append({
                "code": "lane_low_confidence",
                "severity": "warning",
                "detail": (
                    f"initial lane {initial_lane_spec.lane_id!r} had zero evidence; "
                    f"fell back to {lane_id!r}"
                ),
            })
        evidence["_resolved_lane_id"] = lane_id
        return evidence, accumulated_warnings + warnings

    # Every lane failed evidence bar.
    assert last_error is not None  # attempt_order is non-empty
    raise last_error


def _pre_filter_stale_mentions(evidence: dict, target_company: str) -> list[dict]:
    """If selected candidate inputs contain denylisted names that are not the target,
    mutate the evidence to strip/flag them and return a stale_name_filtered warning."""
    warnings: list[dict] = []
    for idx, summary in enumerate(list(evidence["top_accomplishments"])):
        hits = find_stale_company_mentions(summary, target_company)
        if hits:
            warnings.append({
                "code": "stale_name_filtered",
                "severity": "warning",
                "detail": f"stripped mentions {hits} from selected accomplishment #{idx}",
            })
            cleaned = summary
            for name in hits:
                cleaned = re.sub(rf"\b{re.escape(name)}\b", "a prior employer", cleaned, flags=re.IGNORECASE)
            evidence["top_accomplishments"][idx] = cleaned
    for idx, entry in enumerate(list(evidence["question_bank_entries"])):
        answer = entry.get("answer") or ""
        hits = find_stale_company_mentions(answer, target_company)
        if hits:
            warnings.append({
                "code": "stale_name_filtered",
                "severity": "warning",
                "detail": f"dropped question-bank entry referencing {hits}",
            })
            # Drop the whole entry — candidate answers tied to a prior company name
            # carry more context than a surgical edit can preserve.
            evidence["question_bank_entries"][idx] = {"question": "", "answer": ""}
    evidence["question_bank_entries"] = [
        e for e in evidence["question_bank_entries"] if e.get("answer")
    ]
    return warnings
    return record
