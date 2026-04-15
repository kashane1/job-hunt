"""Core file-backed workflow for the Job Hunt repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from .schema_checks import validate
from .simple_yaml import loads as load_yaml

DEFAULT_SKILL_KEYWORDS = [
    "python",
    "ruby",
    "rails",
    "django",
    "flask",
    "fastapi",
    "postgres",
    "sql",
    "aws",
    "gcp",
    "docker",
    "kubernetes",
    "api",
    "backend",
    "platform",
    "infrastructure",
    "automation",
    "ai",
]

DEFAULT_RUNTIME_POLICY = {
    "approval_required_before_submit": True,
    "allow_auto_submit": False,
    "answer_policy": "strict",
    "allow_inferred_answers": True,
    "allow_speculative_answers": False,
    "browser_tabs_soft_limit": 10,
    "browser_tabs_hard_limit": 15,
    "close_background_tabs_aggressively": True,
    "stop_if_confidence_below": 0.75,
    "stop_if_required_fact_missing": True,
}

COMMON_QUESTION_TEMPLATES = [
    "Why are you interested in this role?",
    "What makes you a strong fit for this position?",
    "Are you aligned with the work model and location expectations?",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def display_path(path: Path) -> str:
    resolved = path.resolve()
    root = repo_root().resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = "\n---\n"
    end = text.find(marker, 4)
    if end == -1:
        return {}, text
    frontmatter = text[4:end]
    body = text[end + len(marker) :]
    return load_yaml(frontmatter), body


def read_text_with_fallback(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".yaml", ".yml", ".json"}:
        return path.read_text(encoding="utf-8")
    if suffix in {".docx", ".rtf"} and shutil.which("textutil"):
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    if suffix == ".pdf" and shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    raise ValueError(f"Unsupported or unreadable document format: {path.suffix}")


def load_yaml_file(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default or {}
    return load_yaml(path.read_text(encoding="utf-8"))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.-]{3,}", text.lower())


def unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def heading_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip()
    return fallback


def extract_contact(text: str) -> dict:
    emails = unique_preserve_order(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I))
    phones = unique_preserve_order(
        re.findall(r"(?:\+?\d[\d(). -]{7,}\d)", text)
    )
    links = unique_preserve_order(re.findall(r"https?://[^\s)>]+", text))
    return {"emails": emails, "phones": phones, "links": links}


def extract_bullets(text: str, limit: int = 10) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            bullets.append(stripped[2:].strip())
    return bullets[:limit]


def extract_question_bank(text: str, document_id: str) -> list[dict]:
    matches = re.findall(r"Q:\s*(.*?)\nA:\s*(.*?)(?=\nQ:|\Z)", text, re.S)
    items = []
    for question, answer in matches:
        items.append(
            {
                "question": " ".join(question.split()),
                "answer": " ".join(answer.split()),
                "provenance": "grounded",
                "source_document_ids": [document_id],
            }
        )
    return items


def normalize_profile(profile_root: Path, normalized_root: Path, scoring_config: dict) -> dict:
    ensure_dir(normalized_root)
    raw_root = profile_root / "raw"
    ensure_dir(raw_root)
    skill_keywords = set(scoring_config.get("skill_keywords", DEFAULT_SKILL_KEYWORDS))

    documents = []
    skill_sources: dict[str, set[str]] = defaultdict(set)
    experience_highlights = []
    question_bank = []
    preferences = {
        "target_titles": [],
        "preferred_locations": [],
        "remote_preference": "",
        "excluded_keywords": [],
    }
    contact_totals = {"emails": [], "phones": [], "links": []}
    unreadable_documents = []

    # Normalize every supported source document into a compact profile bundle
    # so later steps can query structured artifacts instead of raw files.
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            raw_text = read_text_with_fallback(path)
        except ValueError as exc:
            unreadable_documents.append({"path": display_path(path), "error": str(exc)})
            continue

        metadata, body = parse_frontmatter(raw_text)
        document_type = (
            metadata.get("document_type")
            or (
                "resume"
                if "resume" in path.name.lower()
                else "cover_letter"
                if "cover" in path.name.lower()
                else "question_bank"
                if "qa" in path.name.lower() or "answer" in path.name.lower()
                else "preferences"
                if "pref" in path.name.lower()
                else "work_note"
            )
        )
        title = str(metadata.get("title") or metadata.get("name") or heading_title(body, path.stem))
        document_id = f"{slugify(path.stem)}-{short_hash(str(path))}"
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        tags = [str(tag).lower() for tag in tags]
        excerpt = " ".join(body.split())[:240]

        documents.append(
            {
                "document_id": document_id,
                "path": display_path(path),
                "document_type": document_type,
                "title": title,
                "tags": tags,
                "source_excerpt": excerpt,
            }
        )

        contact = extract_contact(body)
        for key, values in contact.items():
            contact_totals[key].extend(values)

        for bullet in extract_bullets(body):
            experience_highlights.append(
                {"summary": bullet, "source_document_ids": [document_id]}
            )

        body_tokens = set(tokens(body))
        for skill in skill_keywords:
            if skill in body_tokens or skill in tags:
                skill_sources[skill].add(document_id)

        if document_type == "question_bank":
            question_bank.extend(extract_question_bank(body, document_id))

        if document_type == "preferences":
            preferences["target_titles"] = unique_preserve_order(
                [*preferences["target_titles"], *[str(x) for x in metadata.get("target_titles", [])]]
            )
            preferences["preferred_locations"] = unique_preserve_order(
                [*preferences["preferred_locations"], *[str(x) for x in metadata.get("preferred_locations", [])]]
            )
            preferences["excluded_keywords"] = unique_preserve_order(
                [
                    *preferences["excluded_keywords"],
                    *[str(x).lower() for x in metadata.get("excluded_keywords", [])],
                ]
            )
            if metadata.get("remote_preference"):
                preferences["remote_preference"] = str(metadata["remote_preference"])

    candidate_profile = {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "documents": documents,
        "contact": {
            key: unique_preserve_order(values) for key, values in contact_totals.items()
        },
        "skills": [
            {"name": name, "source_document_ids": sorted(source_ids)}
            for name, source_ids in sorted(skill_sources.items())
        ],
        "experience_highlights": experience_highlights[:25],
        "question_bank": question_bank,
        "preferences": preferences,
        "unreadable_documents": unreadable_documents,
    }

    write_json(normalized_root / "candidate-profile.json", candidate_profile)
    write_json(normalized_root / "skills.json", {"skills": candidate_profile["skills"]})
    write_json(
        normalized_root / "experience-timeline.json",
        {"experience_highlights": candidate_profile["experience_highlights"]},
    )
    write_json(normalized_root / "answer-bank.json", {"question_bank": question_bank})
    write_json(normalized_root / "preferences.json", preferences)
    return candidate_profile


def lead_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = defaultdict(list)
    current = "description"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current = stripped.lstrip("# ").strip().lower()
            continue
        sections[current].append(line)
    return {key: value for key, value in sections.items()}


def extract_requirement_lines(sections: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    items: list[str] = []
    for section_name, lines in sections.items():
        if any(name in section_name for name in names):
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(("- ", "* ")):
                    items.append(stripped[2:].strip())
                elif stripped:
                    items.append(stripped)
    return unique_preserve_order(items)


def extract_lead(input_path: Path, output_dir: Path) -> dict:
    ensure_dir(output_dir)
    if input_path.suffix.lower() == ".json":
        raw = read_json(input_path)
        body = raw.get("raw_description", "")
        metadata = raw
    else:
        raw_text = read_text_with_fallback(input_path)
        metadata, body = parse_frontmatter(raw_text)

    # Leads are stored in one normalized shape regardless of which board or
    # company site they came from, which keeps scoring/reporting generic.
    sections = lead_sections(body)
    title = str(metadata.get("title") or heading_title(body, input_path.stem))
    company = str(metadata.get("company") or metadata.get("organization") or "Unknown Company")
    location = str(metadata.get("location") or "Unknown")
    application_url = str(metadata.get("application_url") or metadata.get("url") or "")
    source = str(metadata.get("source") or "unknown")
    fingerprint = short_hash("|".join([company, title, location, application_url]))
    lead_id = f"{slugify(company)}-{slugify(title)}-{fingerprint}"

    required = extract_requirement_lines(
        sections, ("requirement", "qualification", "must", "about you")
    )
    preferred = extract_requirement_lines(sections, ("preferred", "nice to have", "bonus"))
    keyword_counts = Counter(tokens(f"{title}\n{body}"))
    keywords = [word for word, _ in keyword_counts.most_common(20)]

    lead = {
        "lead_id": lead_id,
        "fingerprint": fingerprint,
        "source": source,
        "application_url": application_url,
        "company": company,
        "title": title,
        "location": location,
        "compensation": str(metadata.get("compensation") or ""),
        "employment_type": str(metadata.get("employment_type") or ""),
        "raw_description": body.strip(),
        "normalized_requirements": {
            "required": required,
            "preferred": preferred,
            "keywords": keywords,
        },
        "fit_assessment": {},
        "status": "discovered",
    }
    write_json(output_dir / f"{lead_id}.json", lead)
    return lead


def _score_title_match(lead_title: str, target_titles: list[str], weight: int) -> tuple[int, list[str]]:
    if not target_titles:
        return 0, []
    lead_tokens = set(tokens(lead_title))
    best_overlap = 0.0
    matched_titles = []
    for title in target_titles:
        title_tokens = set(tokens(title))
        if not title_tokens:
            continue
        overlap = len(lead_tokens & title_tokens) / len(title_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            matched_titles = [title]
    return round(weight * best_overlap), matched_titles


def _score_skills(lead: dict, candidate_profile: dict, weight: int) -> tuple[int, list[str], list[str]]:
    lead_terms = set(tokens("\n".join(lead["normalized_requirements"]["required"])) + lead["normalized_requirements"]["keywords"])
    candidate_skills = {item["name"] for item in candidate_profile.get("skills", [])}
    matched = sorted(candidate_skills & lead_terms)
    missing = sorted(term for term in lead_terms if term in DEFAULT_SKILL_KEYWORDS and term not in candidate_skills)
    denominator = max(len([term for term in lead_terms if term in DEFAULT_SKILL_KEYWORDS]), 1)
    return round(weight * (len(matched) / denominator)), matched, missing


def _score_location(lead: dict, preferences: dict, weight: int) -> tuple[int, str]:
    location = lead.get("location", "").lower()
    preferred_locations = [item.lower() for item in preferences.get("preferred_locations", [])]
    remote_preference = preferences.get("remote_preference", "").lower()
    if "remote" in location and remote_preference in {"remote", "hybrid"}:
        return weight, "remote-compatible"
    if preferred_locations and any(item in location for item in preferred_locations):
        return weight, "preferred-location-match"
    if not preferred_locations and not remote_preference:
        return round(weight * 0.5), "no-location-preference-set"
    return 0, "location-mismatch"


def _score_seniority(lead_title: str, target_titles: list[str], weight: int) -> tuple[int, str]:
    seniority_words = ["staff", "senior", "principal", "lead", "manager"]
    lead_tokens = set(tokens(lead_title))
    preferred_tokens = set(tokens(" ".join(target_titles)))
    matched = [word for word in seniority_words if word in lead_tokens and word in preferred_tokens]
    if matched:
        return weight, matched[0]
    if any(word in lead_tokens for word in seniority_words):
        return round(weight * 0.5), "partial-seniority-match"
    return round(weight * 0.25), "seniority-unspecified"


def _score_domain(lead: dict, candidate_profile: dict, weight: int) -> tuple[int, list[str]]:
    experience_text = " ".join(item["summary"] for item in candidate_profile.get("experience_highlights", []))
    overlap = set(tokens(experience_text)) & set(lead["normalized_requirements"]["keywords"])
    useful = sorted(term for term in overlap if len(term) > 4)[:5]
    if not useful:
        return 0, []
    return min(weight, len(useful) * 2), useful


def _score_compensation(lead: dict, preferences: dict, weight: int) -> tuple[int, str]:
    compensation = lead.get("compensation", "")
    minimum = preferences.get("minimum_compensation", "")
    if not compensation:
        return 0, "compensation-not-listed"
    if not minimum:
        return round(weight * 0.5), "no-minimum-compensation-set"
    match = re.search(r"\d[\d,]*", compensation)
    target = re.search(r"\d[\d,]*", str(minimum))
    if not match or not target:
        return 0, "compensation-unparseable"
    offered = int(match.group(0).replace(",", ""))
    desired = int(target.group(0).replace(",", ""))
    return (weight, "meets-minimum") if offered >= desired else (0, "below-minimum")


def score_lead(lead: dict, candidate_profile: dict, scoring_config: dict) -> dict:
    preferences = candidate_profile.get("preferences", {})
    title_score, matched_titles = _score_title_match(
        lead["title"], preferences.get("target_titles", []), int(scoring_config.get("title_match_weight", 20))
    )
    skills_score, matched_skills, missing_skills = _score_skills(
        lead, candidate_profile, int(scoring_config.get("skills_match_weight", 35))
    )
    seniority_score, seniority_reason = _score_seniority(
        lead["title"], preferences.get("target_titles", []), int(scoring_config.get("seniority_match_weight", 10))
    )
    location_score, location_reason = _score_location(
        lead, preferences, int(scoring_config.get("location_match_weight", 10))
    )
    domain_score, domain_terms = _score_domain(
        lead, candidate_profile, int(scoring_config.get("domain_match_weight", 10))
    )
    compensation_score, compensation_reason = _score_compensation(
        lead, preferences, int(scoring_config.get("compensation_match_weight", 5))
    )

    negative_keywords = [
        keyword.lower()
        for keyword in scoring_config.get("negative_keywords", [])
        + preferences.get("excluded_keywords", [])
    ]
    lead_text = f"{lead['title']} {lead['raw_description']}".lower()
    negative_hits = [keyword for keyword in negative_keywords if keyword and keyword in lead_text]
    penalty = min(
        int(scoring_config.get("negative_keyword_penalty_weight", 10)),
        len(unique_preserve_order(negative_hits)) * 5,
    )

    # The fit score stays intentionally transparent: weighted matches minus a
    # small penalty bucket for obvious deal-breaker keywords.
    fit_score = max(
        0,
        min(
            100,
            title_score
            + skills_score
            + seniority_score
            + location_score
            + domain_score
            + compensation_score
            - penalty,
        ),
    )

    strong_yes_threshold = int(scoring_config.get("strong_yes_threshold", 75))
    maybe_threshold = int(scoring_config.get("maybe_threshold", 55))
    recommendation = (
        "strong_yes"
        if fit_score >= strong_yes_threshold
        else "maybe"
        if fit_score >= maybe_threshold
        else "no"
    )

    assessment = {
        "fit_score": fit_score,
        "fit_recommendation": recommendation,
        "fit_rationale": (
            f"Matched skills: {', '.join(matched_skills) or 'none'}. "
            f"Title alignment: {', '.join(matched_titles) or 'limited'}. "
            f"Location: {location_reason}. "
            f"Penalty keywords: {', '.join(unique_preserve_order(negative_hits)) or 'none'}."
        ),
        "breakdown": {
            "title_match": title_score,
            "skills_match": skills_score,
            "seniority_match": seniority_score,
            "location_match": location_score,
            "domain_match": domain_score,
            "compensation_match": compensation_score,
            "negative_penalty": penalty,
        },
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "matched_titles": matched_titles,
        "negative_hits": unique_preserve_order(negative_hits),
        "domain_terms": domain_terms,
        "seniority_reason": seniority_reason,
        "compensation_reason": compensation_reason,
    }
    lead["fit_assessment"] = assessment
    lead["status"] = "shortlisted" if recommendation in {"strong_yes", "maybe"} else "skipped"
    return lead


def _pick_document(documents: list[dict], document_type: str, lead_keywords: set[str]) -> str:
    candidates = [doc for doc in documents if doc["document_type"] == document_type]
    if not candidates:
        return ""
    ranked = sorted(
        candidates,
        key=lambda doc: len(lead_keywords & set(tokens(" ".join(doc.get("tags", []) + [doc.get("title", "")])))),
        reverse=True,
    )
    return ranked[0]["document_id"]


def _lead_missing_facts(lead: dict, candidate_profile: dict) -> list[str]:
    profile_text = json.dumps(candidate_profile).lower()
    lead_text = f"{lead['title']} {lead['raw_description']}".lower()
    checks = {
        "work authorization": ["authorized", "sponsorship", "visa"],
        "security clearance": ["clearance", "public trust", "polygraph"],
        "salary expectation": ["salary expectation", "compensation expectation"],
        "relocation preference": ["relocate", "relocation"],
        "start date": ["start date", "available to start"],
    }
    missing = []
    for label, keywords in checks.items():
        if any(keyword in lead_text for keyword in keywords) and not any(keyword in profile_text for keyword in keywords):
            missing.append(label)
    return missing


def _build_answers(lead: dict, candidate_profile: dict) -> list[dict]:
    preferences = candidate_profile.get("preferences", {})
    experience = candidate_profile.get("experience_highlights", [])
    question_bank = candidate_profile.get("question_bank", [])
    documents = candidate_profile.get("documents", [])
    document_ids = [doc["document_id"] for doc in documents[:2]]
    fit = lead.get("fit_assessment", {})
    matched_skills = fit.get("matched_skills", [])
    answers = [
        {
            "question": COMMON_QUESTION_TEMPLATES[0],
            "answer": (
                f"I am interested in {lead['title']} at {lead['company']} because the role aligns with "
                f"my background in {', '.join(matched_skills[:3]) or 'relevant backend and platform work'} "
                f"and matches the areas I am actively targeting."
            ),
            "provenance": "synthesized",
            "confidence": 0.84,
            "needs_review": False,
            "source_document_ids": document_ids,
        },
        {
            "question": COMMON_QUESTION_TEMPLATES[1],
            "answer": (
                f"My strongest fit signals are {', '.join(matched_skills[:4]) or 'my transferable engineering experience'}, "
                f"plus achievements such as {experience[0]['summary'] if experience else 'delivering production software across multiple projects'}."
            ),
            "provenance": "synthesized",
            "confidence": 0.82,
            "needs_review": False,
            "source_document_ids": [experience[0]["source_document_ids"][0]] if experience else document_ids,
        },
        {
            "question": COMMON_QUESTION_TEMPLATES[2],
            "answer": (
                f"My current preference is {preferences.get('remote_preference') or 'flexible'}, and the stated location "
                f"for this role is {lead.get('location') or 'not specified'}."
            ),
            "provenance": "grounded" if preferences.get("remote_preference") else "weak_inference",
            "confidence": 0.9 if preferences.get("remote_preference") else 0.55,
            "needs_review": not bool(preferences.get("remote_preference")),
            "source_document_ids": document_ids,
        },
    ]

    lead_tokens = set(tokens(f"{lead['title']} {lead['raw_description']}"))
    for item in question_bank:
        question_tokens = set(tokens(item["question"]))
        if question_tokens & lead_tokens:
            answers.append(
                {
                    "question": item["question"],
                    "answer": item["answer"],
                    "provenance": item["provenance"],
                    "confidence": 0.95,
                    "needs_review": False,
                    "source_document_ids": item["source_document_ids"],
                }
            )
            break

    return answers


def build_application_draft(
    lead: dict,
    candidate_profile: dict,
    runtime_policy: dict,
    output_dir: Path,
) -> dict:
    ensure_dir(output_dir)
    lead_keywords = set(lead["normalized_requirements"]["keywords"])
    documents = candidate_profile.get("documents", [])
    missing_facts = _lead_missing_facts(lead, candidate_profile)
    draft_id = f"{lead['lead_id']}-draft"

    # The draft is the trust boundary before browser execution: all selected
    # assets, answers, provenance, and missing facts are frozen here for review.
    draft = {
        "draft_id": draft_id,
        "lead_id": lead["lead_id"],
        "created_at": now_iso(),
        "approval": {"required": bool(runtime_policy["approval_required_before_submit"]), "approved": False, "reviewer": ""},
        "selected_assets": {
            "resume_document_id": _pick_document(documents, "resume", lead_keywords),
            "cover_letter_document_id": _pick_document(documents, "cover_letter", lead_keywords),
        },
        "prepared_answers": _build_answers(lead, candidate_profile),
        "missing_facts": missing_facts,
        "human_review_summary": (
            "Review missing facts and synthesized answers before submit."
            if missing_facts
            else "Review synthesized answers and approve before submit."
        ),
        "runtime_policy_snapshot": runtime_policy,
        "lead_fit_snapshot": lead.get("fit_assessment", {}),
    }
    write_json(output_dir / f"{draft_id}.json", draft)
    return draft


def quality_from_draft(draft: dict, attempt: dict, runtime_policy: dict) -> tuple[dict, dict]:
    answers = draft.get("prepared_answers", [])
    provenance_counts = Counter(answer["provenance"] for answer in answers)
    average_confidence = (
        sum(float(answer["confidence"]) for answer in answers) / len(answers) if answers else 0.0
    )
    # Application quality is a separate score from job fit. It measures how
    # trustworthy and complete the prepared submission was, not whether the job
    # itself was a good target.
    score = 100
    score -= provenance_counts.get("weak_inference", 0) * 12
    score -= provenance_counts.get("speculative", 0) * 30
    score -= len(draft.get("missing_facts", [])) * 8
    if draft.get("approval", {}).get("required") and not draft.get("approval", {}).get("approved"):
        score -= 25
    if attempt.get("tab_metrics", {}).get("max_open_tabs", 0) > runtime_policy["browser_tabs_soft_limit"]:
        score -= 5
    score = max(0, min(100, score))

    if provenance_counts.get("speculative", 0):
        truthfulness = "fabricated"
    elif provenance_counts.get("weak_inference", 0) or provenance_counts.get("synthesized", 0):
        truthfulness = "inferred"
    else:
        truthfulness = "strict"

    confidence_band = (
        "high" if average_confidence >= 0.85 else "medium" if average_confidence >= 0.65 else "low"
    )
    quality = {
        "application_quality_score": score,
        "confidence_band": confidence_band,
        "truthfulness_rating": truthfulness,
        "average_answer_confidence": round(average_confidence, 3),
    }
    provenance_breakdown = {
        "grounded": provenance_counts.get("grounded", 0),
        "synthesized": provenance_counts.get("synthesized", 0),
        "weak_inference": provenance_counts.get("weak_inference", 0),
        "speculative": provenance_counts.get("speculative", 0),
        "missing_facts": len(draft.get("missing_facts", [])),
    }
    return quality, provenance_breakdown


def report_markdown(report: dict, draft: dict, attempt: dict) -> str:
    lines = [
        f"# Application Report: {report['lead_id']}",
        "",
        "## Summary",
        f"- Status: {report['submission']['status']}",
        f"- Approval obtained: {report['submission']['approval_obtained']}",
        f"- Confirmed submitted: {report['submission']['confirmed_submitted']}",
        f"- Application quality score: {report['quality']['application_quality_score']}",
        f"- Truthfulness rating: {report['quality']['truthfulness_rating']}",
        "",
        "## Answers",
    ]
    for answer in draft.get("prepared_answers", []):
        lines.append(
            f"- {answer['question']} | provenance={answer['provenance']} | confidence={answer['confidence']}"
        )
    lines.extend(
        [
            "",
            "## Missing Facts",
            *([f"- {fact}" for fact in draft.get("missing_facts", [])] or ["- none"]),
            "",
            "## Browser Metrics",
            f"- Max open tabs: {report['browser_metrics']['max_open_tabs']}",
            f"- Soft limit: {report['browser_metrics']['soft_limit']}",
            f"- Hard limit: {report['browser_metrics']['hard_limit']}",
            "",
            "## Attempt Notes",
            f"- Account action: {attempt.get('account_action', 'unknown')}",
            f"- Blocked reason: {attempt.get('blocked_reason', 'none')}",
            f"- Final URL: {attempt.get('final_url', '')}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_application_report(
    draft: dict,
    attempt: dict,
    runtime_policy: dict,
    json_output_dir: Path,
    markdown_output_dir: Path,
) -> dict:
    ensure_dir(json_output_dir)
    ensure_dir(markdown_output_dir)
    quality, provenance_breakdown = quality_from_draft(draft, attempt, runtime_policy)
    # Reports must preserve enough machine-readable detail to explain exactly
    # what happened during an attempt without relying on browser session memory.
    report = {
        "report_id": f"{draft['draft_id']}-report",
        "draft_id": draft["draft_id"],
        "lead_id": draft["lead_id"],
        "generated_at": now_iso(),
        "submission": {
            "attempted": bool(attempt.get("attempted", True)),
            "confirmed_submitted": bool(attempt.get("confirmed_submitted", False)),
            "approval_obtained": bool(draft.get("approval", {}).get("approved", False)),
            "status": (
                "submitted"
                if attempt.get("confirmed_submitted")
                else "blocked"
                if attempt.get("blocked_reason")
                else "attempted"
            ),
        },
        "quality": quality,
        "provenance_breakdown": provenance_breakdown,
        "browser_metrics": {
            "max_open_tabs": int(attempt.get("tab_metrics", {}).get("max_open_tabs", 0)),
            "soft_limit": int(runtime_policy["browser_tabs_soft_limit"]),
            "hard_limit": int(runtime_policy["browser_tabs_hard_limit"]),
        },
        "attempt": attempt,
        "missing_facts": draft.get("missing_facts", []),
    }
    json_path = json_output_dir / f"{report['report_id']}.json"
    write_json(json_path, report)
    markdown_path = markdown_output_dir / f"{report['report_id']}.md"
    markdown_path.write_text(report_markdown(report, draft, attempt), encoding="utf-8")
    return report


def summarize_run(leads_dir: Path, applications_dir: Path, output_dir: Path, markdown_output_dir: Path) -> dict:
    ensure_dir(output_dir)
    ensure_dir(markdown_output_dir)
    leads = [read_json(path) for path in sorted(leads_dir.glob("*.json"))]
    reports = [
        read_json(path)
        for path in sorted(applications_dir.glob("*-report.json"))
    ]

    lead_counts = Counter(lead.get("status", "unknown") for lead in leads)
    application_counts = Counter(report["submission"]["status"] for report in reports)
    quality_scores = [report["quality"]["application_quality_score"] for report in reports]
    average_quality = round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else 0.0
    run_id = f"run-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

    # Run summaries provide a compact operational view across many leads and
    # attempts so the repo can support review and later scoring calibration.
    summary = {
        "run_id": run_id,
        "generated_at": now_iso(),
        "lead_counts": dict(lead_counts),
        "application_counts": dict(application_counts),
        "quality_metrics": {
            "average_application_quality_score": average_quality,
            "reports_count": len(reports),
        },
    }
    write_json(output_dir / f"{run_id}.json", summary)
    lead_lines = [f"- {key}: {value}" for key, value in sorted(lead_counts.items())] or ["- none"]
    application_lines = [f"- {key}: {value}" for key, value in sorted(application_counts.items())] or ["- none"]
    markdown = "\n".join(
        [
            f"# Run Summary: {run_id}",
            "",
            "## Lead Counts",
            *lead_lines,
            "",
            "## Application Counts",
            *application_lines,
            "",
            "## Average Application Quality",
            f"- {average_quality}",
            "",
        ]
    )
    (markdown_output_dir / f"{run_id}.md").write_text(markdown, encoding="utf-8")
    return summary


def verify_artifact(schema_path: Path, artifact_path: Path) -> None:
    schema = read_json(schema_path)
    artifact = read_json(artifact_path)
    validate(artifact, schema)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Job Hunt workflow CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize = subparsers.add_parser("normalize-profile")
    normalize.add_argument("--profile-root", default="profile")
    normalize.add_argument("--normalized-root", default="profile/normalized")
    normalize.add_argument("--scoring-config", default="config/scoring.yaml")

    extract = subparsers.add_parser("extract-lead")
    extract.add_argument("--input", required=True)
    extract.add_argument("--output-dir", default="data/leads")

    score = subparsers.add_parser("score-lead")
    score.add_argument("--lead", required=True)
    score.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    score.add_argument("--scoring-config", default="config/scoring.yaml")

    draft = subparsers.add_parser("build-draft")
    draft.add_argument("--lead", required=True)
    draft.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    draft.add_argument("--runtime-config", default="config/runtime.yaml")
    draft.add_argument("--output-dir", default="data/applications")

    report = subparsers.add_parser("write-report")
    report.add_argument("--draft", required=True)
    report.add_argument("--attempt", required=True)
    report.add_argument("--runtime-config", default="config/runtime.yaml")
    report.add_argument("--json-output-dir", default="data/applications")
    report.add_argument("--markdown-output-dir", default="docs/reports")

    summary = subparsers.add_parser("summarize-run")
    summary.add_argument("--leads-dir", default="data/leads")
    summary.add_argument("--applications-dir", default="data/applications")
    summary.add_argument("--output-dir", default="data/runs")
    summary.add_argument("--markdown-output-dir", default="docs/reports")

    verify = subparsers.add_parser("verify-artifact")
    verify.add_argument("--schema", required=True)
    verify.add_argument("--artifact", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "normalize-profile":
        normalize_profile(
            Path(args.profile_root),
            Path(args.normalized_root),
            load_yaml_file(Path(args.scoring_config), {"skill_keywords": DEFAULT_SKILL_KEYWORDS}),
        )
        return 0

    if args.command == "extract-lead":
        extract_lead(Path(args.input), Path(args.output_dir))
        return 0

    if args.command == "score-lead":
        lead_path = Path(args.lead)
        lead = read_json(lead_path)
        scored = score_lead(
            lead,
            read_json(Path(args.profile)),
            load_yaml_file(Path(args.scoring_config), {}),
        )
        write_json(lead_path, scored)
        return 0

    if args.command == "build-draft":
        draft = build_application_draft(
            read_json(Path(args.lead)),
            read_json(Path(args.profile)),
            {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})},
            Path(args.output_dir),
        )
        print(draft["draft_id"])
        return 0

    if args.command == "write-report":
        write_application_report(
            read_json(Path(args.draft)),
            read_json(Path(args.attempt)),
            {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})},
            Path(args.json_output_dir),
            Path(args.markdown_output_dir),
        )
        return 0

    if args.command == "summarize-run":
        summarize_run(
            Path(args.leads_dir),
            Path(args.applications_dir),
            Path(args.output_dir),
            Path(args.markdown_output_dir),
        )
        return 0

    if args.command == "verify-artifact":
        verify_artifact(Path(args.schema), Path(args.artifact))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
