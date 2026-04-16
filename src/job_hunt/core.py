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

# Re-export shared utilities for backward compatibility.
# New modules should import directly from job_hunt.utils.
from .utils import (  # noqa: F401
    display_path,
    ensure_dir,
    load_yaml_file,
    meaningful_lines,
    now_iso,
    parse_frontmatter,
    read_json,
    repo_root,
    short_hash,
    slugify,
    tokens,
    unique_preserve_order,
    write_json,
)

DEFAULT_SKILL_KEYWORDS = [
    "typescript",
    "javascript",
    "node.js",
    "react",
    "php",
    "python",
    "ruby",
    "rails",
    "django",
    "flask",
    "fastapi",
    "postgres",
    "mysql",
    "sql",
    "aws",
    "s3",
    "ecs",
    "gcp",
    "docker",
    "kubernetes",
    "git",
    "jest",
    "kafka",
    "redis",
    "scss",
    "html",
    "css",
    "kysely",
    "puppeteer",
    "api",
    "backend",
    "platform",
    "infrastructure",
    "automation",
    "ai",
]

DEFAULT_RUNTIME_POLICY = {
    "approval_required_before_submit": True,
    "approval_required_before_account_creation": True,
    "allow_auto_submit": False,
    "answer_policy": "strict",
    "allow_inferred_answers": True,
    "allow_speculative_answers": False,
    "browser_tabs_soft_limit": 10,
    "browser_tabs_hard_limit": 15,
    "close_background_tabs_aggressively": True,
    "stop_if_confidence_below": 0.75,
    "stop_if_required_fact_missing": True,
    "secret_source": "env_or_local_untracked_file",
    "redact_secrets_in_artifacts": True,
}

COMMON_QUESTION_TEMPLATES = [
    "Why are you interested in this role?",
    "What makes you a strong fit for this position?",
    "Are you aligned with the work model and location expectations?",
]

SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "secret",
    "token",
    "otp",
    "one_time_code",
    "verification_code",
    "session",
    "cookie",
    "salary",
    "compensation",
)

CONTACT_DOC_TYPES = {"resume", "cover_letter", "preferences", "question_bank"}
PERSONAL_LINK_PATTERNS = (
    "linkedin.com/in/",
    "github.com/",
    "gitlab.com/",
    "wellfound.com/u/",
)
GENERIC_TITLE_STEMS = {
    "resume",
    "cover-letter",
    "cover-letter2",
    "cover-letter-2",
    "notes",
    "document",
    "untitled",
}
SKILL_ALIASES = {
    "typescript": ("typescript",),
    "javascript": ("javascript",),
    "node.js": ("node.js", "nodejs", "node js"),
    "react": ("react",),
    "php": ("php",),
    "python": ("python",),
    "ruby": ("ruby",),
    "rails": ("rails",),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi",),
    "postgres": ("postgres", "postgresql"),
    "mysql": ("mysql",),
    "sql": ("sql",),
    "aws": ("aws", "amazon web services"),
    "s3": ("s3",),
    "ecs": ("ecs",),
    "gcp": ("gcp", "google cloud"),
    "docker": ("docker",),
    "kubernetes": ("kubernetes", "k8s"),
    "git": ("git",),
    "jest": ("jest",),
    "kafka": ("kafka",),
    "redis": ("redis",),
    "scss": ("scss",),
    "html": ("html",),
    "css": ("css",),
    "kysely": ("kysely",),
    "puppeteer": ("puppeteer",),
    "api": ("api", "apis"),
    "backend": ("backend", "back-end"),
    "frontend": ("frontend", "front-end"),
    "platform": ("platform",),
    "infrastructure": ("infrastructure",),
    "automation": ("automation", "automated"),
    "ai": ("ai", "artificial intelligence"),
}
SKILL_ALIAS_PATTERNS = {
    skill: tuple(re.compile(rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])") for alias in aliases)
    for skill, aliases in SKILL_ALIASES.items()
}
REMOTE_POSITIVE_PATTERNS = (
    re.compile(r"\bremote[-\s]?friendly\b", re.I),
    re.compile(r"\bfully remote\b", re.I),
    re.compile(r"\bremote setup\b", re.I),
    re.compile(r"\bremote[-\s]?first\b", re.I),
    re.compile(r"\bthis role is remote\b", re.I),
)
REMOTE_HYBRID_PATTERNS = (
    re.compile(r"\bhybrid\b", re.I),
    re.compile(r"\bremote or hybrid\b", re.I),
)
TARGET_TITLE_PATTERNS = (
    re.compile(r"\b(?:seeking|looking for|targeting)\s+(?:a|an)?\s*([A-Za-z][A-Za-z /-]{4,60}?(?:engineer|developer|programmer|architect|manager|lead))\s+role\b", re.I),
    re.compile(r"\bmy next (?:position|role).{0,80}?\b(?:as|is)\s+(?:a|an)?\s*([A-Za-z][A-Za-z /-]{4,60}?(?:engineer|developer|programmer|architect|manager|lead))\b", re.I),
)
COMPENSATION_PATTERN = re.compile(r"\$ ?(\d[\d,]{4,})")


def read_text_with_fallback(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".yaml", ".yml", ".json"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf" and shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    raise ValueError(f"Unsupported or unreadable document format for v1: {path.suffix}")


def heading_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            candidate = stripped.lstrip("# ").strip()
            if candidate.lower() in {"overview", "summary"}:
                continue
            return candidate
    return fallback



def clean_url(url: str) -> str:
    return url.rstrip('",.;)]}')


def extract_links(text: str) -> list[str]:
    return unique_preserve_order(clean_url(url) for url in re.findall(r"https?://[^\s)>]+", text))


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def extract_candidate_contact(text: str, document_type: str) -> dict:
    if document_type not in CONTACT_DOC_TYPES:
        return {"emails": [], "phones": [], "links": []}

    header_text = "\n".join(meaningful_lines(text, limit=8))
    emails = unique_preserve_order(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", header_text, re.I))

    raw_phones = re.findall(
        r"(?<!\d)(?:\+?1[-.\s]*)?(?:\(?\d{3}\)?[-.\s]*)\d{3}[-.\s]*\d{4}(?!\d)",
        header_text,
    )
    phones = unique_preserve_order(
        normalized
        for normalized in (normalize_phone(phone) for phone in raw_phones)
        if normalized
    )

    links = []
    for link in extract_links(header_text):
        lowered = link.lower()
        if any(pattern in lowered for pattern in PERSONAL_LINK_PATTERNS):
            links.append(link)
    return {"emails": emails, "phones": phones, "links": unique_preserve_order(links)}


def select_candidate_contact(documents: list[dict]) -> dict:
    priority = {"resume": 0, "cover_letter": 1, "preferences": 2, "question_bank": 3}
    ranked = sorted(
        documents,
        key=lambda item: (
            priority.get(item.get("document_type", ""), 99),
            item.get("metrics", {}).get("line_count", 10_000),
        ),
    )
    emails: list[str] = []
    phones: list[str] = []
    links: list[str] = []
    for item in ranked:
        contact = item.get("extracted_contact", {})
        emails.extend(contact.get("emails", []))
        phones.extend(contact.get("phones", []))
        links.extend(contact.get("links", []))
    return {
        "emails": unique_preserve_order(emails),
        "phones": unique_preserve_order(phones),
        "links": unique_preserve_order(links),
    }


def extract_bullets(text: str, limit: int = 10) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            candidate = stripped[2:].strip()
        elif re.match(r"^\d+[.)]\s+", stripped):
            candidate = re.sub(r"^\d+[.)]\s+", "", stripped).strip()
        else:
            continue
        if candidate.startswith("http://") or candidate.startswith("https://"):
            continue
        bullets.append(candidate)
    return bullets[:limit]


def is_question_like(line: str) -> bool:
    stripped = line.strip()
    lowered = stripped.lower()
    if not stripped or len(stripped) > 200:
        return False
    if stripped.endswith("?"):
        return True
    if re.search(r"\bcharacters about me", lowered):
        return True
    return bool(
        re.match(
            r"^(describe|what|why|how|which|can you|do you|tell me|expected annual cash compensation)\b",
            lowered,
        )
    )


def normalize_question(question: str) -> str:
    cleaned = " ".join(question.replace("…", "...").split())
    return cleaned.rstrip(":").strip()


def looks_like_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if stripped.startswith(("http://", "https://")):
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return True
    if re.fullmatch(r"(yes|no|applied)", lowered):
        return True
    if re.fullmatch(r"[A-Z]{1,6}-\d{1,5}", stripped):
        return True
    if re.fullmatch(r"[_-]{3,}", stripped):
        return True
    return False


def extract_prompt_answer_pairs(text: str, document_id: str) -> list[dict]:
    lines = meaningful_lines(text)
    items: list[dict] = []
    index = 0
    while index < len(lines):
        prompt = lines[index]
        if not is_question_like(prompt):
            index += 1
            continue
        index += 1
        answer_lines: list[str] = []
        while index < len(lines):
            current = lines[index]
            if is_question_like(current):
                break
            if looks_like_noise_line(current):
                if answer_lines:
                    break
                index += 1
                continue
            answer_lines.append(current)
            index += 1
        answer = " ".join(answer_lines).strip()
        if len(answer) < 25:
            continue
        items.append(
            {
                "question": normalize_question(prompt),
                "answer": answer,
                "provenance": "grounded",
                "source_document_ids": [document_id],
            }
        )
    return items


def extract_question_bank(text: str, document_id: str, document_type: str) -> list[dict]:
    matches = re.findall(r"Q:\s*(.*?)\nA:\s*(.*?)(?=\nQ:|\Z)", text, re.S)
    items = []
    for question, answer in matches:
        items.append(
            {
                "question": normalize_question(question),
                "answer": " ".join(answer.split()),
                "provenance": "grounded",
                "source_document_ids": [document_id],
            }
        )
    if document_type in {"cover_letter", "question_bank"}:
        existing = {(entry["question"], entry["answer"]) for entry in items}
        for item in extract_prompt_answer_pairs(text, document_id):
            key = (item["question"], item["answer"])
            if key not in existing:
                items.append(item)
                existing.add(key)
    return items


def infer_document_title(path: Path, metadata: dict, body: str) -> str:
    if metadata.get("title") or metadata.get("name"):
        return str(metadata.get("title") or metadata.get("name"))
    stem = path.stem.strip()
    if stem and slugify(stem) not in GENERIC_TITLE_STEMS:
        return stem
    return heading_title(body, stem or "Untitled")


def title_case_phrase(text: str) -> str:
    return " ".join(word.capitalize() if word.islower() else word for word in text.split())


def classify_document_type(path: Path, metadata: dict, body: str) -> tuple[str, str]:
    explicit = metadata.get("document_type")
    if explicit:
        return str(explicit), "explicit"

    lower_name = path.name.lower()
    lower_body = body.lower()
    if "cover" in lower_name or "dear hiring manager" in lower_body:
        return "cover_letter", "inferred"
    if "qa" in lower_name or "question answers" in lower_body or re.search(r"(^|\n)q:\s*", lower_body):
        return "question_bank", "inferred"
    if "pref" in lower_name:
        return "preferences", "inferred"
    if (
        "knowledge transfer" in lower_body
        or "this document summarizes" in lower_body
        or "architecture" in lower_body
        or "what are seat map templates" in lower_body
    ):
        return "project_note", "inferred"
    if "resume" in lower_name or (
        "professional experience" in lower_body and "technical skills" in lower_body
    ):
        return "resume", "inferred"
    if "work notes" in lower_name or re.search(r"(^|\n)#?\s*(january|february|march|april|may|june|july|august|september|october|november|december)\b", lower_body):
        return "work_note", "inferred"
    return "work_note", "fallback"


def count_headings(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith("#"))


def extract_skill_hits(text: str, tags: list[str], configured_keywords: set[str]) -> list[str]:
    lowered_text = text.lower()
    tag_set = {tag.lower() for tag in tags}
    hits = set()
    for keyword in configured_keywords:
        lowered_keyword = keyword.lower()
        if lowered_keyword in tag_set:
            hits.add(lowered_keyword)
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(lowered_keyword)}(?![a-z0-9])", lowered_text):
            hits.add(lowered_keyword)
    for skill, patterns in SKILL_ALIAS_PATTERNS.items():
        if skill in tag_set:
            hits.add(skill)
            continue
        if any(pattern.search(lowered_text) for pattern in patterns):
            hits.add(skill)
    return sorted(hits)


def infer_preferences_from_answers(question_bank: list[dict]) -> dict:
    preferences: dict[str, object] = {}
    titles: list[str] = []
    preferred_locations: list[str] = []

    for item in question_bank:
        question = item.get("question", "")
        answer = item.get("answer", "")
        q_lower = question.lower()

        if "compensation" in q_lower or "salary" in q_lower:
            match = COMPENSATION_PATTERN.search(answer)
            if match:
                preferences["minimum_compensation"] = f"${int(match.group(1).replace(',', '')):,}"

        if "search timeline" in q_lower:
            preferences["search_timeline"] = answer

        if "next position" in q_lower or "next role" in q_lower:
            for pattern in TARGET_TITLE_PATTERNS:
                for match in pattern.finditer(answer):
                    candidate = " ".join(match.group(1).split())
                    if len(candidate.split()) <= 8:
                        titles.append(title_case_phrase(candidate))
            if any(pattern.search(answer) for pattern in REMOTE_POSITIVE_PATTERNS):
                preferences["remote_preference"] = "remote"
                preferred_locations.append("Remote")
            elif any(pattern.search(answer) for pattern in REMOTE_HYBRID_PATTERNS):
                preferences["remote_preference"] = "hybrid"

    if titles:
        preferences["target_titles"] = unique_preserve_order(titles)
    if preferred_locations:
        preferences["preferred_locations"] = unique_preserve_order(preferred_locations)
    return preferences


def extract_metric_phrases(text: str, limit: int = 8) -> list[str]:
    matches = re.findall(r"([^\n.]*?(?:\d[\d,]*%|\$\d[\d,]*|\d+\+ years|\d+\+|\d+\s*(?:mins?|minutes|hours?|days?|weeks?|months?|years?)|100%)[^\n.]*)", text, re.I)
    cleaned = unique_preserve_order(" ".join(match.split()) for match in matches)
    return [item for item in cleaned if len(item) >= 12][:limit]


def count_dates(text: str) -> int:
    return len(
        re.findall(
            r"\b(?:20\d{2}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
            text,
            re.I,
        )
    )


def extract_role_and_company_lines(text: str, limit: int = 6) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = " ".join(line.strip().split())
        if not stripped:
            continue
        if "|" in stripped and any(token in stripped.lower() for token in ("inc", "llc", "corp", "full-time", "contract")):
            items.append(stripped)
        elif re.search(r"\b(engineer|developer|analyst|manager|lead|platform|backend|frontend)\b", stripped, re.I):
            items.append(stripped)
        if len(items) >= limit:
            break
    return unique_preserve_order(items)


def extract_highlights(text: str, limit: int = 10) -> list[str]:
    bullets = extract_bullets(text, limit=limit)
    if bullets:
        return bullets

    highlights: list[str] = []
    for line in meaningful_lines(text):
        if looks_like_noise_line(line):
            continue
        if is_question_like(line):
            continue
        if len(line) < 30:
            continue
        if ":" in line and len(line.split()) <= 4:
            continue
        highlights.append(line)
        if len(highlights) >= limit:
            break
    return highlights


def document_score_band(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def score_document(
    document_type: str,
    metadata: dict,
    body: str,
    tags: list[str],
    contact: dict,
    bullets: list[str],
    question_bank_items: list[dict],
    skill_hits: list[str],
) -> tuple[dict, dict, list[str]]:
    word_count = len(body.split())
    line_count = len(body.splitlines())
    heading_count = count_headings(body)
    metric_phrases = extract_metric_phrases(body)
    metric_count = len(metric_phrases)
    date_mentions = count_dates(body)
    role_lines = extract_role_and_company_lines(body)

    structure_score = min(
        30,
        (8 if metadata else 0)
        + min(10, heading_count * 3)
        + min(8, len(bullets))
        + (4 if heading_title(body, "").strip() else 0),
    )
    signal_score = min(
        35,
        min(15, metric_count * 5)
        + min(10, len(skill_hits) * 2)
        + min(6, len(question_bank_items) * 3)
        + min(4, len(role_lines) * 2),
    )
    coverage_score = min(
        20,
        (4 if word_count >= 150 else 0)
        + (4 if word_count >= 400 else 0)
        + (4 if word_count >= 1_000 else 0)
        + (4 if word_count >= 2_500 else 0)
        + min(4, date_mentions),
    )
    hygiene_score = 15
    if word_count < 120:
        hygiene_score -= 5
    if word_count > 20_000 and heading_count < 8:
        hygiene_score -= 6
    if document_type == "resume" and not contact["emails"]:
        hygiene_score -= 4
    if document_type in {"cover_letter", "question_bank"} and word_count > 2_500:
        hygiene_score -= 3
    quality_score = max(0, min(100, structure_score + signal_score + coverage_score + hygiene_score))

    quantity_score = max(
        0,
        min(
            100,
            min(40, round(word_count / 80))
            + min(20, len(bullets) * 2)
            + min(20, metric_count * 4)
            + min(20, len(question_bank_items) * 5),
        ),
    )

    type_value = {
        "resume": 30,
        "cover_letter": 22,
        "question_bank": 24,
        "preferences": 24,
        "project_note": 24,
        "work_note": 14,
    }.get(document_type, 14)
    value_score = max(
        0,
        min(
            100,
            type_value
            + min(20, metric_count * 4)
            + min(18, len(skill_hits) * 2)
            + min(14, len(bullets) * 2)
            + min(12, len(question_bank_items) * 4)
            + min(6, len(role_lines) * 2),
        ),
    )

    suggestions: list[str] = []
    if not metadata:
        suggestions.append("Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.")
    if document_type == "work_note" and word_count > 10_000:
        suggestions.append("Split this large work-note file into smaller monthly or project-specific docs to improve retrieval quality.")
    if document_type == "cover_letter" and word_count > 1_500:
        suggestions.append("Separate cover-letter drafts from answer snippets so tailored application assets are easier to select.")
    if metric_count == 0 and document_type in {"resume", "cover_letter", "work_note", "project_note"}:
        suggestions.append("Add more quantified outcomes so achievements are easier to ground in applications.")
    if not skill_hits and document_type in {"resume", "project_note", "work_note"}:
        suggestions.append("Mention key technologies explicitly to make skill extraction less lossy.")
    if not question_bank_items and document_type == "question_bank":
        suggestions.append("Convert this into explicit `Q:` / `A:` pairs for direct answer-bank reuse.")

    metrics = {
        "word_count": word_count,
        "line_count": line_count,
        "heading_count": heading_count,
        "bullet_count": len(bullets),
        "question_count": len(question_bank_items),
        "metric_count": metric_count,
        "date_mentions": date_mentions,
        "role_line_count": len(role_lines),
        "contact_points": sum(len(values) for values in contact.values()),
    }
    scores = {
        "quality": quality_score,
        "quality_band": document_score_band(quality_score),
        "quantity": quantity_score,
        "quantity_band": document_score_band(quantity_score),
        "value": value_score,
        "value_band": document_score_band(value_score),
    }
    signals = {
        "metric_phrases": metric_phrases,
        "role_lines": role_lines,
    }
    return metrics, scores, suggestions


def normalize_profile_documents(
    raw_root: Path,
    normalized_root: Path,
    skill_keywords: set[str],
) -> tuple[list[dict], dict]:
    documents_dir = normalized_root / "documents"
    ensure_dir(documents_dir)

    normalized_documents: list[dict] = []
    audit_documents: list[dict] = []
    unreadable_documents: list[dict] = []
    aggregated = {
        "documents": [],
        "skill_sources": defaultdict(set),
        "experience_highlights": [],
        "question_bank": [],
        "preferences": {
            "target_titles": [],
            "preferred_locations": [],
            "remote_preference": "",
            "excluded_keywords": [],
        },
        "contact_totals": {"emails": [], "phones": [], "links": []},
    }

    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            raw_text = read_text_with_fallback(path)
        except ValueError as exc:
            unreadable_documents.append({"path": display_path(path), "error": str(exc)})
            continue

        metadata, body = parse_frontmatter(raw_text)
        document_type, type_source = classify_document_type(path, metadata, body)
        title = infer_document_title(path, metadata, body)
        document_id = f"{slugify(path.stem)}-{short_hash(str(path))}"
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        tags = [str(tag).lower() for tag in tags]
        excerpt = " ".join(body.split())[:240]
        contact = extract_candidate_contact(body, document_type)
        bullets = extract_highlights(body)
        question_bank_items = extract_question_bank(body, document_id, document_type)
        skill_hits = extract_skill_hits(body, tags, skill_keywords)
        metrics, scores, suggestions = score_document(
            document_type,
            metadata,
            body,
            tags,
            contact,
            bullets,
            question_bank_items,
            skill_hits,
        )

        doc_record = {
            "document_id": document_id,
            "path": display_path(path),
            "document_type": document_type,
            "document_type_source": type_source,
            "title": title,
            "tags": tags,
            "source_excerpt": excerpt,
        }
        aggregated["documents"].append(doc_record)

        for key, values in contact.items():
            aggregated["contact_totals"][key].extend(values)
        for bullet in bullets:
            aggregated["experience_highlights"].append(
                {"summary": bullet, "source_document_ids": [document_id]}
            )
        for skill in skill_hits:
            aggregated["skill_sources"][skill].add(document_id)
        aggregated["question_bank"].extend(question_bank_items)

        if document_type == "preferences":
            aggregated["preferences"]["target_titles"] = unique_preserve_order(
                [*aggregated["preferences"]["target_titles"], *[str(x) for x in metadata.get("target_titles", [])]]
            )
            aggregated["preferences"]["preferred_locations"] = unique_preserve_order(
                [*aggregated["preferences"]["preferred_locations"], *[str(x) for x in metadata.get("preferred_locations", [])]]
            )
            aggregated["preferences"]["excluded_keywords"] = unique_preserve_order(
                [
                    *aggregated["preferences"]["excluded_keywords"],
                    *[str(x).lower() for x in metadata.get("excluded_keywords", [])],
                ]
            )
            if metadata.get("remote_preference"):
                aggregated["preferences"]["remote_preference"] = str(metadata["remote_preference"])

        normalized_document = {
            **doc_record,
            "metadata": metadata,
            "extracted_contact": contact,
            "skill_hits": skill_hits,
            "experience_highlights": bullets[:12],
            "question_bank": question_bank_items,
            "metrics": metrics,
            "scores": scores,
            "suggestions": suggestions,
        }
        write_json(documents_dir / f"{document_id}.json", normalized_document)
        normalized_documents.append(normalized_document)

        audit_documents.append(
            {
                "document_id": document_id,
                "path": display_path(path),
                "title": title,
                "document_type": document_type,
                "document_type_source": type_source,
                "scores": scores,
                "metrics": metrics,
                "skill_hits": skill_hits[:12],
                "top_highlights": bullets[:3],
                "top_questions": [item["question"] for item in question_bank_items[:3]],
                "suggestions": suggestions,
            }
        )

    audit = {
        "generated_at": now_iso(),
        "raw_document_count": len(normalized_documents) + len(unreadable_documents),
        "supported_document_count": len(normalized_documents),
        "unreadable_documents": unreadable_documents,
        "documents": sorted(audit_documents, key=lambda item: (-item["scores"]["value"], item["title"].lower())),
    }
    if audit["documents"]:
        quality_average = round(
            sum(item["scores"]["quality"] for item in audit["documents"]) / len(audit["documents"]), 1
        )
        quantity_average = round(
            sum(item["scores"]["quantity"] for item in audit["documents"]) / len(audit["documents"]), 1
        )
        value_average = round(
            sum(item["scores"]["value"] for item in audit["documents"]) / len(audit["documents"]), 1
        )
    else:
        quality_average = quantity_average = value_average = 0.0
    audit["summary"] = {
        "average_quality": quality_average,
        "average_quantity": quantity_average,
        "average_value": value_average,
        "high_value_document_count": sum(1 for item in audit["documents"] if item["scores"]["value"] >= 80),
        "high_quality_document_count": sum(1 for item in audit["documents"] if item["scores"]["quality"] >= 80),
        "documents_needing_attention": [
            item["document_id"]
            for item in audit["documents"]
            if item["scores"]["quality"] < 60 or item["scores"]["value"] < 60
        ],
    }
    return normalized_documents, {
        "documents": normalized_documents,
        "audit": audit,
        "aggregated": aggregated,
        "unreadable_documents": unreadable_documents,
    }


def write_profile_audit_report(audit: dict, output_path: Path) -> None:
    ensure_dir(output_path.parent)
    lines = [
        "# Profile Document Audit",
        "",
        f"- Generated at: {audit['generated_at']}",
        f"- Raw documents scanned: {audit['raw_document_count']}",
        f"- Supported documents normalized: {audit['supported_document_count']}",
        f"- Average quality: {audit['summary']['average_quality']}",
        f"- Average quantity: {audit['summary']['average_quantity']}",
        f"- Average value: {audit['summary']['average_value']}",
        "",
        "## Highest-Value Documents",
    ]
    for item in audit["documents"][:5]:
        lines.extend(
            [
                f"### {item['title']}",
                f"- Path: {item['path']}",
                f"- Type: {item['document_type']} ({item['document_type_source']})",
                f"- Scores: quality {item['scores']['quality']}, quantity {item['scores']['quantity']}, value {item['scores']['value']}",
                f"- Skill hits: {', '.join(item['skill_hits']) or 'none'}",
                f"- Suggestions: {'; '.join(item['suggestions']) or 'none'}",
                "",
            ]
        )
    lines.append("## All Documents")
    lines.append("")
    for item in audit["documents"]:
        lines.extend(
            [
                f"### {item['title']}",
                f"- Path: {item['path']}",
                f"- Type: {item['document_type']}",
                f"- Metrics: {item['metrics']['word_count']} words, {item['metrics']['bullet_count']} bullets, {item['metrics']['metric_count']} quantified phrases, {item['metrics']['question_count']} Q/A pairs",
                f"- Scores: quality {item['scores']['quality']} ({item['scores']['quality_band']}), quantity {item['scores']['quantity']} ({item['scores']['quantity_band']}), value {item['scores']['value']} ({item['scores']['value_band']})",
                f"- Highlights: {' | '.join(item['top_highlights']) or 'none extracted'}",
                f"- Suggestions: {'; '.join(item['suggestions']) or 'none'}",
                "",
            ]
        )
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


COMPLETENESS_CHECKS = [
    ("contact_email", "Contact email", lambda p: bool(p.get("contact", {}).get("emails"))),
    ("contact_phone", "Contact phone", lambda p: bool(p.get("contact", {}).get("phones"))),
    ("contact_linkedin", "LinkedIn URL", lambda p: any("linkedin.com" in link for link in p.get("contact", {}).get("links", []))),
    ("resume_document", "Resume document", lambda p: any(d["document_type"] == "resume" for d in p.get("documents", []))),
    ("work_history", "Work history with dates", lambda p: any(d["document_type"] == "resume" for d in p.get("documents", []))),
    ("skills_extracted", "Skills inventory (5+)", lambda p: len(p.get("skills", [])) >= 5),
    ("question_bank_populated", "Answer bank (3+ answers)", lambda p: len(p.get("question_bank", [])) >= 3),
    ("target_titles", "Target job titles", lambda p: bool(p.get("preferences", {}).get("target_titles"))),
    ("remote_preference", "Remote/hybrid/onsite preference", lambda p: bool(p.get("preferences", {}).get("remote_preference"))),
    ("compensation_range", "Compensation expectations", lambda p: bool(p.get("preferences", {}).get("minimum_compensation"))),
    ("preferred_locations", "Preferred locations", lambda p: bool(p.get("preferences", {}).get("preferred_locations"))),
    ("work_authorization", "Work authorization status", lambda p: bool(p.get("preferences", {}).get("work_authorization"))),
    ("quantified_achievements", "Quantified achievements (3+)", lambda p: sum(1 for h in p.get("experience_highlights", []) if re.search(r"\d+[%$KkMm]|\d{2,}", h.get("summary", ""))) >= 3),
    ("project_case_study", "Project case study", lambda p: any(d["document_type"] == "project_note" for d in p.get("documents", []))),
    ("preferences_document", "Preferences document", lambda p: any(d["document_type"] == "preferences" for d in p.get("documents", []))),
    ("excluded_keywords", "Deal-breaker exclusions", lambda p: bool(p.get("preferences", {}).get("excluded_keywords"))),
]


def check_profile_completeness(profile: dict, audit: dict) -> dict:
    results = []
    passed = 0
    for check_id, label, check_fn in COMPLETENESS_CHECKS:
        ok = bool(check_fn(profile))
        results.append({"check": check_id, "label": label, "passed": ok})
        if ok:
            passed += 1
    total = len(COMPLETENESS_CHECKS)
    score = round(100 * passed / total) if total else 0
    if score >= 80:
        readiness = "ready"
    elif score >= 60:
        readiness = "needs_work"
    else:
        readiness = "not_ready"
    return {
        "generated_at": now_iso(),
        "completeness_score": score,
        "readiness": readiness,
        "passed": passed,
        "total": total,
        "checks": results,
        "missing": [r["label"] for r in results if not r["passed"]],
    }


def write_completeness_report(completeness: dict, output_path: Path) -> None:
    ensure_dir(output_path.parent)
    lines = [
        "# Profile Completeness Report",
        "",
        f"- Generated at: {completeness['generated_at']}",
        f"- Completeness score: {completeness['completeness_score']}%",
        f"- Readiness: {completeness['readiness']}",
        f"- Checks passed: {completeness['passed']} / {completeness['total']}",
        "",
        "## Results",
        "",
    ]
    for check in completeness["checks"]:
        icon = "PASS" if check["passed"] else "MISS"
        lines.append(f"- [{icon}] {check['label']}")
    if completeness["missing"]:
        lines.extend(["", "## Missing Signals", ""])
        for label in completeness["missing"]:
            lines.append(f"- {label}")
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def normalize_profile(profile_root: Path, normalized_root: Path, scoring_config: dict) -> dict:
    ensure_dir(normalized_root)
    raw_root = profile_root / "raw"
    ensure_dir(raw_root)
    skill_keywords = set(scoring_config.get("skill_keywords", DEFAULT_SKILL_KEYWORDS))
    reports_root = profile_root.parent / "docs" / "reports"
    _, normalized_bundle = normalize_profile_documents(raw_root, normalized_root, skill_keywords)
    aggregated = normalized_bundle["aggregated"]
    audit = normalized_bundle["audit"]
    inferred_preferences = infer_preferences_from_answers(aggregated["question_bank"])
    merged_preferences = {
        "target_titles": unique_preserve_order(
            [*aggregated["preferences"]["target_titles"], *[str(x) for x in inferred_preferences.get("target_titles", [])]]
        ),
        "preferred_locations": unique_preserve_order(
            [*aggregated["preferences"]["preferred_locations"], *[str(x) for x in inferred_preferences.get("preferred_locations", [])]]
        ),
        "remote_preference": aggregated["preferences"]["remote_preference"] or str(inferred_preferences.get("remote_preference", "")),
        "excluded_keywords": aggregated["preferences"]["excluded_keywords"],
    }
    for optional_key in ("minimum_compensation", "search_timeline"):
        if inferred_preferences.get(optional_key):
            merged_preferences[optional_key] = inferred_preferences[optional_key]

    candidate_profile = {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "documents": aggregated["documents"],
        "contact": select_candidate_contact(normalized_bundle["documents"]),
        "skills": [
            {"name": name, "source_document_ids": sorted(source_ids)}
            for name, source_ids in sorted(aggregated["skill_sources"].items())
        ],
        "experience_highlights": aggregated["experience_highlights"][:25],
        "question_bank": aggregated["question_bank"],
        "preferences": merged_preferences,
        "unreadable_documents": normalized_bundle["unreadable_documents"],
    }

    write_json(normalized_root / "candidate-profile.json", candidate_profile)
    write_json(normalized_root / "skills.json", {"skills": candidate_profile["skills"]})
    write_json(
        normalized_root / "experience-timeline.json",
        {"experience_highlights": candidate_profile["experience_highlights"]},
    )
    write_json(normalized_root / "answer-bank.json", {"question_bank": aggregated["question_bank"]})
    write_json(normalized_root / "preferences.json", merged_preferences)
    completeness = check_profile_completeness(candidate_profile, audit)
    write_json(normalized_root / "completeness.json", completeness)
    write_json(normalized_root / "document-audit.json", audit)
    write_profile_audit_report(audit, reports_root / "profile-document-audit.md")
    write_completeness_report(completeness, reports_root / "profile-completeness.md")
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
        "approval": {
            "final_submit": {
                "required": bool(runtime_policy["approval_required_before_submit"]),
                "approved": False,
                "reviewer": "",
            },
            "account_creation": {
                "required": bool(runtime_policy.get("approval_required_before_account_creation", True)),
                "approved": False,
                "reviewer": "",
            },
        },
        "selected_assets": {
            "resume_document_id": _pick_document(documents, "resume", lead_keywords),
            "cover_letter_document_id": _pick_document(documents, "cover_letter", lead_keywords),
        },
        "prepared_answers": _build_answers(lead, candidate_profile),
        "missing_facts": missing_facts,
        "human_review_summary": (
            "Review missing facts, synthesized answers, and any account-creation approval before browser execution."
            if missing_facts
            else "Review synthesized answers and approve account creation if needed before final submit."
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
    final_submit_approval = draft.get("approval", {}).get("final_submit", {})
    if final_submit_approval.get("required") and not final_submit_approval.get("approved"):
        score -= 25
    account_creation_approval = draft.get("approval", {}).get("account_creation", {})
    if attempt.get("account_action") == "created" and account_creation_approval.get("required") and not account_creation_approval.get("approved"):
        score -= 15
    peak_open_tabs = int(
        attempt.get("tab_metrics", {}).get(
            "peak_open_tabs", attempt.get("tab_metrics", {}).get("max_open_tabs", 0)
        )
    )
    if peak_open_tabs > runtime_policy["browser_tabs_soft_limit"]:
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


def approval_snapshot(draft: dict, stage: str) -> dict:
    approval = draft.get("approval", {}).get(stage, {})
    return {
        "required": bool(approval.get("required", False)),
        "obtained": bool(approval.get("approved", False)),
        "reviewer": str(approval.get("reviewer", "")),
    }


def redact_sensitive_data(value, path: str = "") -> tuple[object, list[str]]:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        redacted_paths: list[str] = []
        for key, item in value.items():
            current_path = f"{path}.{key}" if path else key
            if any(keyword in key.lower() for keyword in SENSITIVE_KEYWORDS):
                redacted[key] = "[REDACTED]"
                redacted_paths.append(current_path)
                continue
            cleaned_item, child_paths = redact_sensitive_data(item, current_path)
            redacted[key] = cleaned_item
            redacted_paths.extend(child_paths)
        return redacted, redacted_paths
    if isinstance(value, list):
        cleaned_items = []
        redacted_paths: list[str] = []
        for index, item in enumerate(value):
            cleaned_item, child_paths = redact_sensitive_data(item, f"{path}[{index}]")
            cleaned_items.append(cleaned_item)
            redacted_paths.extend(child_paths)
        return cleaned_items, redacted_paths
    return value, []


def browser_metrics(attempt: dict, runtime_policy: dict) -> dict:
    raw_metrics = attempt.get("tab_metrics", {})
    peak_open_tabs = int(raw_metrics.get("peak_open_tabs", raw_metrics.get("max_open_tabs", 0)))
    opened = int(raw_metrics.get("opened", peak_open_tabs))
    closed_for_budget = int(raw_metrics.get("closed_for_budget", 0))
    hard_limit_hit = bool(raw_metrics.get("hard_limit_hit", peak_open_tabs >= int(runtime_policy["browser_tabs_hard_limit"])))
    return {
        "opened": opened,
        "peak_open_tabs": peak_open_tabs,
        "closed_for_budget": closed_for_budget,
        "hard_limit_hit": hard_limit_hit,
        "soft_limit": int(runtime_policy["browser_tabs_soft_limit"]),
        "hard_limit": int(runtime_policy["browser_tabs_hard_limit"]),
    }


def attempt_status(attempt: dict) -> str:
    if attempt.get("confirmed_submitted"):
        return "submitted"
    if attempt.get("submit_confirmed") is False or attempt.get("submission_ambiguity"):
        return "ambiguous"
    if attempt.get("blocked_reason"):
        return "blocked"
    if attempt.get("attempted", True):
        return "attempted"
    return "not_started"


def checkpoint_records(report_generated_at: str) -> list[dict]:
    return [
        {"name": "pre_browser", "status": "recorded", "recorded_at": report_generated_at},
        {"name": "pre_submit", "status": "recorded", "recorded_at": report_generated_at},
        {"name": "terminal_outcome", "status": "recorded", "recorded_at": report_generated_at},
    ]


def report_markdown(report: dict, draft: dict, attempt: dict) -> str:
    lines = [
        f"# Application Report: {report['lead_id']}",
        "",
        "## Summary",
        f"- Status: {report['submission']['status']}",
        f"- Final submit approval required: {report['submission']['final_submit_approval_required']}",
        f"- Final submit approval obtained: {report['submission']['final_submit_approval_obtained']}",
        f"- Account creation approval required: {report['submission']['account_creation_approval_required']}",
        f"- Account creation approval obtained: {report['submission']['account_creation_approval_obtained']}",
        f"- Submit attempted: {report['submission']['submit_attempted']}",
        f"- Confirmed submitted: {report['submission']['confirmed_submitted']}",
        f"- Application quality score: {report['quality']['application_quality_score']}",
        f"- Truthfulness rating: {report['quality']['truthfulness_rating']}",
        "",
        "## Answers",
    ]
    for answer in report.get("answers_used", []):
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
            f"- Tabs opened: {report['browser_metrics']['opened']}",
            f"- Peak open tabs: {report['browser_metrics']['peak_open_tabs']}",
            f"- Tabs closed for budget: {report['browser_metrics']['closed_for_budget']}",
            f"- Hard limit hit: {report['browser_metrics']['hard_limit_hit']}",
            f"- Soft limit: {report['browser_metrics']['soft_limit']}",
            f"- Hard limit: {report['browser_metrics']['hard_limit']}",
            "",
            "## Attempt Notes",
            f"- Account action: {attempt.get('account_action', 'unknown')}",
            f"- Blocked reason: {attempt.get('blocked_reason', 'none')}",
            f"- Final URL: {attempt.get('final_url', '')}",
            f"- Secrets redacted: {report['redaction']['applied']}",
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
    if runtime_policy.get("redact_secrets_in_artifacts", True):
        attempt_payload, redacted_fields = redact_sensitive_data(attempt)
    else:
        attempt_payload, redacted_fields = attempt, []
    generated_at = now_iso()
    final_submit = approval_snapshot(draft, "final_submit")
    account_creation = approval_snapshot(draft, "account_creation")
    answers_used = draft.get("prepared_answers", [])
    browser_audit = browser_metrics(attempt_payload, runtime_policy)
    # Reports must preserve enough machine-readable detail to explain exactly
    # what happened during an attempt without relying on browser session memory.
    report = {
        "report_id": f"{draft['draft_id']}-report",
        "draft_id": draft["draft_id"],
        "lead_id": draft["lead_id"],
        "generated_at": generated_at,
        "submission": {
            "attempted": bool(attempt.get("attempted", True)),
            "confirmed_submitted": bool(attempt.get("confirmed_submitted", False)),
            "submit_attempted": bool(attempt.get("attempted", True)),
            "final_submit_approval_required": final_submit["required"],
            "final_submit_approval_obtained": final_submit["obtained"],
            "account_creation_approval_required": account_creation["required"],
            "account_creation_approval_obtained": account_creation["obtained"],
            "status": attempt_status(attempt_payload),
        },
        "quality": quality,
        "provenance_breakdown": provenance_breakdown,
        "browser_metrics": browser_audit,
        "answers_used": answers_used,
        "checkpoints": checkpoint_records(generated_at),
        "blockers": [attempt_payload["blocked_reason"]] if attempt_payload.get("blocked_reason") else [],
        "redaction": {
            "applied": bool(runtime_policy.get("redact_secrets_in_artifacts", True)),
            "fields_redacted": redacted_fields,
        },
        "attempt": attempt_payload,
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
    approvals_recorded = sum(
        1
        for report in reports
        if "final_submit_approval_required" in report.get("submission", {})
    )
    confirmed_submissions = sum(
        1 for report in reports if report.get("submission", {}).get("confirmed_submitted")
    )
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
            "approval_records_count": approvals_recorded,
            "confirmed_submissions": confirmed_submissions,
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

    audit = subparsers.add_parser("audit-profile-docs")
    audit.add_argument("--profile-root", default="profile")
    audit.add_argument("--normalized-root", default="profile/normalized")
    audit.add_argument("--scoring-config", default="config/scoring.yaml")

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

    # --- Tracking commands ---
    update_st = subparsers.add_parser("update-status", help="Advance an application to a new stage")
    update_st.add_argument("--lead", required=True, help="Path to lead JSON file")
    update_st.add_argument("--stage", required=True, help="New stage name")
    update_st.add_argument("--note", default="", help="Optional note for this transition")
    update_st.add_argument("--status-dir", default="data/applications", help="Directory for status files")

    subparsers.add_parser("check-status", help="Show current status for a lead").add_argument(
        "--lead", required=True, help="Path to lead JSON file"
    )

    list_apps = subparsers.add_parser("list-applications", help="List applications with optional filters")
    list_apps.add_argument("--stage", default="", help="Filter by stage name")
    list_apps.add_argument("--since", default="", help="Filter by created_at >= date (ISO format)")
    list_apps.add_argument("--status-dir", default="data/applications", help="Directory for status files")

    integrity = subparsers.add_parser("check-integrity", help="Detect orphaned content and dangling references")
    integrity.add_argument("--data-root", default="data", help="Root data directory")

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

    if args.command == "audit-profile-docs":
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

    # --- Tracking commands ---
    if args.command == "update-status":
        from .tracking import create_application_status, update_application_status

        lead = read_json(Path(args.lead))
        lead_id = lead["lead_id"]
        status_dir = Path(args.status_dir)
        status_path = status_dir / f"{lead_id}-status.json"
        if not status_path.exists():
            create_application_status(lead_id, status_dir)
        result = update_application_status(status_path, args.stage, args.note)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "check-status":
        from .tracking import check_status

        lead = read_json(Path(args.lead))
        lead_id = lead["lead_id"]
        status_path = Path("data/applications") / f"{lead_id}-status.json"
        result = check_status(status_path)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "list-applications":
        from .tracking import list_applications

        result = list_applications(Path(args.status_dir), args.stage, args.since)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "check-integrity":
        from .tracking import check_integrity

        result = check_integrity(Path(args.data_root))
        print(json.dumps(result, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
