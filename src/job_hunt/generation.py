"""Content generation for resumes, cover letters, and answer sets."""

from __future__ import annotations

import re
from pathlib import Path

from .utils import ensure_dir, now_iso, short_hash, slugify, tokens, write_json

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

    for style in variant_styles:
        selected_acc = select_accomplishments_for_variant(highlights, lead_keywords, style)
        selected_sk = select_skills_for_variant(skills, lead_keywords, style)
        md_content = render_resume_markdown(candidate_profile, selected_acc, selected_sk, style, lead)

        lead_slug = slugify(f"{lead.get('company', 'unknown')}-{lead.get('title', 'role')}")
        content_id = f"{lead_slug}-{style}-{ts_compact}"

        record = {
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
            "provenance": "grounded",
        }
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

def generate_cover_letter(
    lead: dict,
    candidate_profile: dict,
    company_research: dict | None,
    output_dir: Path,
) -> dict:
    """Generate a cover letter assembled from grounded profile data."""
    ensure_dir(output_dir)
    highlights = candidate_profile.get("experience_highlights", [])
    skills = candidate_profile.get("skills", [])
    preferences = candidate_profile.get("preferences", {})
    contact = candidate_profile.get("contact", {})
    documents = candidate_profile.get("documents", [])
    doc_ids = [d["document_id"] for d in documents[:3]]
    candidate_name = preferences.get("candidate_name", "Candidate")

    title = lead.get("title", "the role")
    company = lead.get("company", "your company")

    lead_keywords = set(generation_tokens(
        f"{lead.get('title', '')} "
        f"{' '.join(lead.get('normalized_requirements', {}).get('keywords', []))} "
        f"{' '.join(lead.get('normalized_requirements', {}).get('required', []))}"
    ))

    # Select top skills and accomplishment.
    top_skills = select_skills_for_variant(skills, lead_keywords, STYLE_IMPACT_FOCUSED, limit=4)
    top_accomplishments = select_accomplishments_for_variant(highlights, lead_keywords, STYLE_IMPACT_FOCUSED, limit=2)

    # Opening.
    opening = (
        f"I am writing to express my interest in the {title} position at {company}. "
        f"With deep experience in {', '.join(top_skills[:3]) or 'relevant engineering domains'}, "
        f"I am confident in my ability to contribute meaningfully to your team."
    )

    # Body 1: skills + accomplishment.
    accomplishment_text = top_accomplishments[0] if top_accomplishments else "delivering production software"
    body1 = (
        f"My strongest qualifications include expertise in {', '.join(top_skills[:4])}. "
        f"A representative achievement: {accomplishment_text}."
    )

    # Body 2: company-specific or role-specific.
    if company_research:
        industry = company_research.get("industry", "")
        tech = ", ".join(company_research.get("tech_stack", [])[:3])
        if industry or tech:
            body2 = (
                f"I am particularly drawn to {company}"
                f"{' in the ' + industry + ' space' if industry else ''}"
                f"{', especially the use of ' + tech if tech else ''}. "
                f"This aligns well with my background and career goals."
            )
        else:
            body2 = (
                f"I am drawn to the mission of {company} and the opportunity "
                f"to apply my skills to the challenges described in this role."
            )
    else:
        body2 = (
            f"The {title} role at {company} aligns well with my career trajectory, "
            f"and I look forward to bringing my experience to your team."
        )

    # Closing.
    remote_pref = preferences.get("remote_preference", "flexible")
    closing = (
        f"I am available for {remote_pref} work and eager to discuss how my background "
        f"can support {company}'s goals. Thank you for your consideration."
    )

    md_content = f"""Dear Hiring Manager,

{opening}

{body1}

{body2}

{closing}

Sincerely,
{candidate_name}
"""

    ts = now_iso()
    lead_slug = slugify(f"{lead.get('company', 'unknown')}-{lead.get('title', 'role')}")
    ts_compact = ts.replace(":", "").replace("-", "").replace("+", "").replace("T", "T")[:15] or ts
    content_id = f"{lead_slug}-cover-letter-{ts_compact}"

    record = {
        "content_id": content_id,
        "content_type": "cover_letter",
        "variant_style": "default",
        "generated_at": ts,
        "lead_id": lead.get("lead_id", ""),
        "job_title": lead.get("title", ""),
        "source_document_ids": doc_ids,
        "selected_accomplishments": top_accomplishments[:2],
        "selected_skills": top_skills,
        "output_path": str(output_dir / f"{content_id}.md"),
        "provenance": "synthesized",
    }
    write_json(output_dir / f"{content_id}.json", record)
    (output_dir / f"{content_id}.md").write_text(md_content, encoding="utf-8")
    return record
