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

# Policy constant — lifted to module scope per the discovery-hardening plan.
# Without this filter, `extract_lead.keywords` is just the top-N most frequent
# tokens (dominated by "the", "and", "with", "you"), which inflates the
# keyword-density stuffing check in ats_check.check_resume because every
# normal resume naturally contains these. Skills/tech terms are what we
# actually want to score fit and ATS alignment against.
KEYWORD_STOPWORDS: frozenset[str] = frozenset({
    "and", "the", "with", "for", "you", "our", "your", "are", "will",
    "not", "but", "can", "has", "have", "had", "was", "were", "been",
    "this", "that", "these", "those", "from", "into", "onto", "over",
    "than", "then", "there", "their", "them", "they", "what", "when",
    "which", "while", "who", "whom", "whose", "why", "how", "all",
    "any", "each", "every", "some", "such", "other", "others",
    "per", "via", "also", "just", "like", "out", "off", "up", "down",
    "its", "it.s", "i.e", "e.g", "etc", "etc.",
    "role", "team", "teams", "work", "working", "works", "worked",
    "company", "companies", "job", "jobs", "position", "positions",
    "candidate", "candidates", "applicant", "applicants",
    "we", "us", "we.re", "we.ve", "i.m", "you.re", "you.ll",
    "new", "strong", "able", "experience", "experienced",
    "including", "include", "includes", "included",
    "using", "use", "used", "uses",
    "building", "build", "built", "builds",
    "ability", "years", "year", "opportunity", "opportunities",
    "skills", "skilled", "knowledge", "understanding",
    "across", "within", "through", "during", "because", "should",
    "would", "could", "may", "might", "must", "need", "needs", "needed",
    "one", "two", "three", "first", "second", "third", "last",
})

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

# Profile-side helpers extracted to profile.py in Phase 1b to break the would-be
# application.py ↔ core.py import cycle. Re-exported here so existing callers
# (tests, CLI handlers) keep working without churn.
from .profile import (  # noqa: F401
    COMMON_QUESTION_TEMPLATES,
    COMPLETENESS_CHECKS,
    build_application_draft,
    check_profile_completeness,
    write_completeness_report,
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
    # Batch 4 — autonomous Indeed application. The v4 policy invariant is
    # `auto_submit_tiers = []`: the agent fills forms but never clicks Submit.
    # Runtime overrides can tighten (force field-by-field review) but cannot
    # loosen (AGENTS.md Safety Overrides). Tiers describe the depth of human
    # field-level review required BEFORE the human clicks Submit themselves.
    "apply_policy": {
        "default_tier": "tier_2",
        "auto_submit_tiers": [],
        "tier_1_requirements": {
            "all_answers_supported": True,
            "ats_check_status": "passed",
            "no_account_creation": True,
            "preflight_not_already_applied": True,
        },
        "inter_application_delay_seconds": [60, 120],
        "inter_application_pacing_distribution": "log_normal",
        "inter_application_coffee_break_every_n": 5,
        "inter_application_daily_cap": 20,
        "score_floor": None,
        "confirmation_email_timeout_minutes": 30,
        "stale_attempt_threshold_minutes": 45,
        "indeed_search_result_cap_per_run": 50,
        "batch_size_cap": 10,
        "retention_days": 365,
        "allow_account_creation": False,
        "gmail_query_window_days": 14,
    },
}

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
            if metadata.get("work_authorization"):
                aggregated["preferences"]["work_authorization"] = str(metadata["work_authorization"])
            if "sponsorship_required" in metadata:
                aggregated["preferences"]["sponsorship_required"] = bool(metadata["sponsorship_required"])

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
    if aggregated["preferences"].get("work_authorization"):
        merged_preferences["work_authorization"] = aggregated["preferences"]["work_authorization"]
    if "sponsorship_required" in aggregated["preferences"]:
        merged_preferences["sponsorship_required"] = aggregated["preferences"]["sponsorship_required"]

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
    # Prefer canonical_url (stripped of tracking params) in the fingerprint so that
    # the same posting fetched via different URL variants produces the same lead_id.
    # Falls back to application_url for leads created without canonicalization.
    fingerprint_url = str(metadata.get("canonical_url") or application_url)
    fingerprint = short_hash("|".join([company, title, location, fingerprint_url]))
    lead_id = f"{slugify(company)}-{slugify(title)}-{fingerprint}"

    required = extract_requirement_lines(
        sections, ("requirement", "qualification", "must", "about you")
    )
    preferred = extract_requirement_lines(sections, ("preferred", "nice to have", "bonus"))
    keyword_counts = Counter(tokens(f"{title}\n{body}"))
    keywords = [
        word
        for word, _ in keyword_counts.most_common(100)
        if word not in KEYWORD_STOPWORDS and len(word) >= 3
    ][:20]

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
    # Preserve batch 2 ingestion provenance fields if present in frontmatter
    for optional_field in ("ingestion_method", "ingested_at", "canonical_url", "ingestion_notes"):
        value = metadata.get(optional_field)
        if value:
            lead[optional_field] = str(value)
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
    draft.add_argument("--resume-variant", default="", help="Content ID of the resume variant to use")

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

    retier = subparsers.add_parser(
        "recompute-tiers",
        help="Back-fill tier on status records previously demoted only for ATS warnings",
    )
    retier.add_argument(
        "--applications-dir", default="data/applications",
        help="Directory containing application status records",
    )

    # --- Generation commands ---
    gen_resume = subparsers.add_parser("generate-resume", help="Generate tailored resume variants for a lead")
    gen_resume.add_argument("--lead", required=True, help="Path to lead JSON file")
    gen_resume.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    gen_resume.add_argument("--variants", default="technical_depth,impact_focused,breadth",
                            help="Comma-separated variant styles")
    gen_resume.add_argument("--output-dir", default="data/generated/resumes")

    gen_answers = subparsers.add_parser("generate-answers", help="Generate answers for application questions")
    gen_answers.add_argument("--lead", required=True, help="Path to lead JSON file")
    gen_answers.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    gen_answers.add_argument("--questions", default="", help="Comma-separated questions")
    gen_answers.add_argument("--questions-file", default="", help="Path to JSON array of questions")
    gen_answers.add_argument("--runtime-config", default="config/runtime.yaml")
    gen_answers.add_argument("--output-dir", default="data/generated/answers")

    gen_cl = subparsers.add_parser("generate-cover-letter", help="Generate a tailored cover letter")
    gen_cl.add_argument("--lead", required=True, help="Path to lead JSON file")
    gen_cl.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    gen_cl.add_argument("--company", default="", help="Path to company research JSON")
    gen_cl.add_argument("--output-dir", default="data/generated/cover-letters")
    gen_cl.add_argument(
        "--lane",
        choices=["auto", "platform_internal_tools", "ai_engineer", "product_minded_engineer"],
        default="auto",
        help="Cover-letter strength lane. 'auto' picks based on lead signal.",
    )

    # --- Research commands ---
    res_co = subparsers.add_parser("research-company", help="Create company research scaffold")
    res_co.add_argument("--lead", default="", help="Path to lead JSON file")
    res_co.add_argument("--company", default="", help="Company name (if no lead)")
    res_co.add_argument("--output-dir", default="data/companies")

    score_co = subparsers.add_parser("score-company-fit", help="Score company fit against profile")
    score_co.add_argument("--company", required=True, help="Path to company research JSON")
    score_co.add_argument("--profile", default="profile/normalized/candidate-profile.json")

    # --- Follow-up commands ---
    check_fu = subparsers.add_parser("check-follow-ups", help="List applications due for follow-up")
    check_fu.add_argument("--status-dir", default="data/applications")
    check_fu.add_argument("--format", default="json", choices=["json", "text"])

    gen_fu = subparsers.add_parser("generate-follow-up", help="Generate a follow-up draft")
    gen_fu.add_argument("--lead", required=True, help="Path to lead JSON file")
    gen_fu.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    gen_fu.add_argument("--output-dir", default="data/generated/follow-ups")

    # Batch 2 Phase 1: PDF export (on-demand, requires optional weasyprint extra)
    export_pdf_parser = subparsers.add_parser(
        "export-pdf", help="Render a generated markdown to PDF via weasyprint"
    )
    export_pdf_group = export_pdf_parser.add_mutually_exclusive_group(required=True)
    export_pdf_group.add_argument("--content-record", help="Path to generated-content JSON (primary)")
    export_pdf_group.add_argument("--content-id", help="Content ID to resolve under data/generated/")
    export_pdf_parser.add_argument("--data-root", default="data")

    # Batch 2 Phase 2: URL-based lead ingestion
    ingest_parser = subparsers.add_parser(
        "ingest-url", help="Fetch a job posting from a URL and create a lead"
    )
    ingest_group = ingest_parser.add_mutually_exclusive_group(required=True)
    ingest_group.add_argument("--url", help="Job posting URL (Greenhouse/Lever/generic HTML)")
    ingest_group.add_argument("--urls-file", help="File with one URL per line (batch mode)")
    ingest_parser.add_argument("--html-file", help="Pre-downloaded HTML (bypasses network fetch)")
    ingest_parser.add_argument("--output-dir", default="data/leads")
    ingest_parser.add_argument("--max-workers", type=int, default=5, help="Batch mode worker cap")

    # Batch 2 Phase 3: ATS compatibility checker (standalone; CLI also runs after generate-*)
    ats_parser = subparsers.add_parser(
        "ats-check", help="Validate a generated resume/cover-letter for ATS compatibility"
    )
    ats_group = ats_parser.add_mutually_exclusive_group(required=True)
    ats_group.add_argument("--content-record", help="Path to generated-content JSON (primary)")
    ats_group.add_argument("--content-id", help="Content ID to resolve under data/generated/")
    ats_parser.add_argument("--lead", help="Path to lead JSON for keyword coverage checks (optional)")
    ats_parser.add_argument("--data-root", default="data")
    ats_parser.add_argument("--output-dir", default="data/generated/ats-checks")
    ats_parser.add_argument("--target-pages", type=int, default=1, help="Max pages for resume (default 1)")

    # Batch 2 Phase 3: add --skip-ats-check flag to generate-resume / generate-cover-letter
    gen_resume.add_argument(
        "--skip-ats-check",
        action="store_true",
        help="Skip automatic ATS compatibility check after generation (default: run)",
    )
    gen_cl.add_argument(
        "--skip-ats-check",
        action="store_true",
        help="Skip automatic ATS compatibility check after generation (default: run)",
    )

    # Batch 2 Phase 4: pipeline analytics dashboard
    dash_parser = subparsers.add_parser(
        "apps-dashboard",
        help="Report applications-per-week, callback rate, variant win rates, stage conversions",
    )
    dash_parser.add_argument("--data-root", default="data")
    dash_parser.add_argument("--since", default="", help="ISO date cutoff (e.g. 2026-04-01)")
    dash_parser.add_argument("--weeks", type=int, default=None, help="Shorthand for since = now - N weeks")

    # Batch 2 Phase 5a: skills gap analyzer
    gap_parser = subparsers.add_parser(
        "analyze-skills-gap",
        help="Identify required skills missing from the candidate profile across scored leads",
    )
    gap_parser.add_argument("--data-root", default="data")
    gap_parser.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    gap_parser.add_argument("--taxonomy", default="config/skills-taxonomy.yaml")
    gap_parser.add_argument("--excluded", default="profile/skills-excluded.yaml")

    # Batch 2 Phase 5b: rejection pattern analyzer
    rej_parser = subparsers.add_parser(
        "analyze-rejections",
        help="Find patterns in terminal applications (rejected, ghosted, withdrawn)",
    )
    rej_parser.add_argument("--data-root", default="data")

    # ----- Batch 3: active job discovery -----
    disc_parser = subparsers.add_parser(
        "discover-jobs",
        help="Poll the watchlist for new openings (Greenhouse/Lever/careers)",
    )
    disc_parser.add_argument("--watchlist", default="config/watchlist.yaml")
    disc_parser.add_argument("--leads-dir", default="data/leads")
    disc_parser.add_argument("--discovery-root", default="data/discovery")
    disc_parser.add_argument("--max-ingest", type=int, default=50)
    disc_parser.add_argument("--max-workers", type=int, default=3)
    disc_parser.add_argument("--sources", default="", help="Comma-separated: greenhouse,lever,careers")
    disc_parser.add_argument("--dry-run", action="store_true")
    disc_parser.add_argument("--no-score", action="store_true")
    disc_parser.add_argument("--score-concurrency", type=int, default=3)
    disc_parser.add_argument(
        "--reset-cursor", default="",
        help="'Company|source' or 'Company|*' to clear cursor before running",
    )
    disc_parser.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    disc_parser.add_argument("--scoring-config", default="config/scoring.yaml")

    state_parser = subparsers.add_parser(
        "discovery-state",
        help="Show the discovery cursor or query the most recent run artifact",
    )
    state_parser.add_argument("--discovery-root", default="data/discovery")
    state_parser.add_argument("--company", default="")
    state_parser.add_argument("--source", default="")
    state_parser.add_argument("--last-run", action="store_true")
    state_parser.add_argument("--bucket", default="")

    wls_parser = subparsers.add_parser("watchlist-show", help="Print watchlist contents as JSON")
    wls_parser.add_argument("--watchlist", default="config/watchlist.yaml")
    wls_parser.add_argument("--company", default="")

    wla_parser = subparsers.add_parser("watchlist-add", help="Add a company to the watchlist")
    wla_parser.add_argument("--watchlist", default="config/watchlist.yaml")
    wla_parser.add_argument("--name", required=True)
    wla_parser.add_argument("--greenhouse", default="")
    wla_parser.add_argument("--lever", default="")
    wla_parser.add_argument("--careers-url", default="")
    wla_parser.add_argument("--indeed-search-url", default="")
    wla_parser.add_argument("--notes", default="")
    wla_parser.add_argument(
        "--force", action="store_true",
        help="Override comment-loss warning when the target file has comments",
    )

    wlr_parser = subparsers.add_parser("watchlist-remove", help="Remove a company from the watchlist")
    wlr_parser.add_argument("--watchlist", default="config/watchlist.yaml")
    wlr_parser.add_argument("--name", required=True)
    wlr_parser.add_argument("--force", action="store_true")

    wlv_parser = subparsers.add_parser("watchlist-validate", help="Check watchlist against schema")
    wlv_parser.add_argument("--watchlist", default="config/watchlist.yaml")

    rl_parser = subparsers.add_parser("review-list", help="List low-confidence discovery entries awaiting review")
    rl_parser.add_argument("--discovery-root", default="data/discovery")
    rl_parser.add_argument("--status", default="pending")

    rp_parser = subparsers.add_parser("review-promote", help="Ingest a low-confidence entry into data/leads/")
    rp_parser.add_argument("entry_id")
    rp_parser.add_argument("--discovery-root", default="data/discovery")
    rp_parser.add_argument("--leads-dir", default="data/leads")

    rd_parser = subparsers.add_parser("review-dismiss", help="Mark a low-confidence entry as dismissed")
    rd_parser.add_argument("entry_id")
    rd_parser.add_argument("--discovery-root", default="data/discovery")
    rd_parser.add_argument("--reason", default="")

    rcc_parser = subparsers.add_parser("robots-cache-clear", help="Flush the robots.txt cache")
    rcc_parser.add_argument("--discovery-root", default="data/discovery")

    # ----- Batch 4 Phase 1b: introspection + preflight -----
    subparsers.add_parser(
        "schemas-list",
        help="List all JSON schemas shipped with the repo (for agent self-validation)",
    )

    schemas_show = subparsers.add_parser(
        "schemas-show",
        help="Print a schema body by name (e.g. application-plan, answer-bank)",
    )
    schemas_show.add_argument("--name", required=True)

    preflight = subparsers.add_parser(
        "apply-preflight",
        help="Batch 4: run pre-application readiness checks (profile, answer-bank, session, lock)",
    )
    preflight.add_argument("--runtime-config", default="config/runtime.yaml")

    # ----- Batch 4 Phase 2: answer bank mutations + queries -----
    ab_bank_arg_default = "data/answer-bank.json"

    ab_list_pending = subparsers.add_parser(
        "answer-bank-list-pending",
        help="List inferred/stale bank entries awaiting human review",
    )
    ab_list_pending.add_argument("--bank", default=ab_bank_arg_default)
    ab_list_pending.add_argument("--since", default="")
    ab_list_pending.add_argument(
        "--report",
        default="docs/reports/answer-bank-pending.md",
        help="Path to render the pending-review markdown report",
    )

    ab_list = subparsers.add_parser(
        "answer-bank-list",
        help="Enumerate answer-bank entries (optionally filtered by status)",
    )
    ab_list.add_argument("--bank", default=ab_bank_arg_default)
    ab_list.add_argument("--status", default="")
    ab_list.add_argument("--since", default="")

    ab_show = subparsers.add_parser(
        "answer-bank-show",
        help="Print a single bank entry by id",
    )
    ab_show.add_argument("--bank", default=ab_bank_arg_default)
    ab_show.add_argument("--entry-id", required=True)

    ab_validate = subparsers.add_parser(
        "answer-bank-validate",
        help="Schema-check the bank and replay the audit log for tamper detection",
    )
    ab_validate.add_argument("--bank", default=ab_bank_arg_default)

    ab_promote = subparsers.add_parser(
        "answer-bank-promote",
        help="Flip an inferred entry to curated/reviewed",
    )
    ab_promote.add_argument("--bank", default=ab_bank_arg_default)
    ab_promote.add_argument("--entry-id", required=True)
    ab_promote.add_argument("--answer", required=True)
    ab_promote.add_argument("--notes", default="")
    ab_promote.add_argument("--dry-run", action="store_true")

    ab_deprecate = subparsers.add_parser(
        "answer-bank-deprecate",
        help="Mark a bank entry deprecated (resolve() will skip it)",
    )
    ab_deprecate.add_argument("--bank", default=ab_bank_arg_default)
    ab_deprecate.add_argument("--entry-id", required=True)
    ab_deprecate.add_argument("--reason", required=True)
    ab_deprecate.add_argument("--dry-run", action="store_true")

    # ----- Batch 4 Phase 4: application preparation + run -----
    prep = subparsers.add_parser(
        "prepare-application",
        help="Build plan.json + status.json for a scored lead",
    )
    prep.add_argument("--lead", required=True, help="Path to lead JSON file")
    prep.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    prep.add_argument("--runtime-config", default="config/runtime.yaml")
    prep.add_argument("--output-root", default="data/applications")
    prep.add_argument("--data-root", default="data")
    prep.add_argument("--force", action="store_true")
    prep.add_argument("--dry-run", action="store_true")

    post = subparsers.add_parser(
        "apply-posting",
        help="Emit the agent handoff bundle for a prepared draft",
    )
    post.add_argument("--draft-id", required=True)
    post.add_argument("--data-root", default="data")
    post.add_argument("--dry-run", action="store_true")

    rec_att = subparsers.add_parser(
        "record-attempt",
        help="Persist an attempt record and merge into status.json",
    )
    rec_att.add_argument("--draft-id", required=True)
    rec_att.add_argument("--attempt-file", required=True)
    rec_att.add_argument("--data-root", default="data")
    rec_att.add_argument("--dry-run", action="store_true")

    app_status = subparsers.add_parser(
        "apply-status",
        help="Print the status.json for a draft",
    )
    app_status.add_argument("--draft-id", required=True)
    app_status.add_argument("--data-root", default="data")

    recon = subparsers.add_parser(
        "reconcile-applications",
        help="Write unknown_outcome replacements for stale in_progress attempts",
    )
    recon.add_argument("--runtime-config", default="config/runtime.yaml")
    recon.add_argument("--data-root", default="data")
    recon.add_argument("--current-batch-id", default="")

    dlist = subparsers.add_parser(
        "draft-list",
        help="Enumerate application drafts with optional filters",
    )
    dlist.add_argument("--data-root", default="data")
    dlist.add_argument("--tier", default="")
    dlist.add_argument("--status", default="")
    dlist.add_argument("--source", default="")

    refresh = subparsers.add_parser(
        "refresh-application",
        help="Re-snapshot the profile into plan.json without regenerating resume",
    )
    refresh.add_argument("--draft-id", required=True)
    refresh.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    refresh.add_argument("--data-root", default="data")
    refresh.add_argument("--dry-run", action="store_true")

    cpt = subparsers.add_parser(
        "checkpoint-update",
        help="Lightweight mid-form checkpoint advance (no schema re-validation)",
    )
    cpt.add_argument("--draft-id", required=True)
    cpt.add_argument("--attempt-id", required=True, help="Attempt filename")
    cpt.add_argument("--checkpoint", required=True)
    cpt.add_argument("--screenshot", default="")
    cpt.add_argument("--data-root", default="data")

    mae = subparsers.add_parser(
        "mark-applied-externally",
        help="Record that the user applied manually outside the tool",
    )
    mae.add_argument("--lead-id", required=True)
    mae.add_argument("--applied-at", default="")
    mae.add_argument("--note", default="")
    mae.add_argument("--data-root", default="data")
    mae.add_argument("--dry-run", action="store_true")

    wdr = subparsers.add_parser(
        "withdraw-application",
        help="Withdraw an application (user retracted)",
    )
    wdr.add_argument("--draft-id", required=True)
    wdr.add_argument("--reason", required=True)
    wdr.add_argument("--data-root", default="data")
    wdr.add_argument("--dry-run", action="store_true")

    rop = subparsers.add_parser(
        "reopen-application",
        help="Clear unknown_outcome / failed so the draft is picked up again",
    )
    rop.add_argument("--draft-id", required=True)
    rop.add_argument("--data-root", default="data")
    rop.add_argument("--dry-run", action="store_true")

    # ----- Batch 4 Phase 7: batch orchestration -----
    ab = subparsers.add_parser(
        "apply-batch",
        help="Prepare the next N leads as a cohesive batch (lock, pacing, pipelining)",
    )
    ab.add_argument("--top", type=int, required=True)
    ab.add_argument("--floor", type=float, default=None)
    ab.add_argument("--source", default="indeed")
    ab.add_argument("--runtime-config", default="config/runtime.yaml")
    ab.add_argument("--profile", default="profile/normalized/candidate-profile.json")
    ab.add_argument("--leads-dir", default="data/leads")
    ab.add_argument("--data-root", default="data")
    ab.add_argument("--dry-run", action="store_true")

    bl = subparsers.add_parser("batch-list", help="List batch runs")
    bl.add_argument("--data-root", default="data")
    bl.add_argument("--active", action="store_true")
    bl.add_argument("--since", default="")

    bs = subparsers.add_parser("batch-status", help="Show live batch progress + summary")
    bs.add_argument("--batch-id", required=True)
    bs.add_argument("--data-root", default="data")

    bc = subparsers.add_parser("batch-cancel", help="Cooperative abort of a running batch")
    bc.add_argument("--batch-id", required=True)
    bc.add_argument("--data-root", default="data")
    bc.add_argument("--dry-run", action="store_true")

    # ----- Batch 4 Phase 8: Gmail-driven confirmation -----
    ic = subparsers.add_parser(
        "ingest-confirmation",
        help="Parse a single Gmail confirmation email and update status.json",
    )
    ic.add_argument("--draft-id", default="", help="Optional; auto-correlated when omitted")
    ic.add_argument("--gmail-message-file", required=True, help="Path to JSON Gmail payload OR raw .eml")
    ic.add_argument("--data-root", default="data")
    ic.add_argument("--dry-run", action="store_true")

    pc = subparsers.add_parser(
        "poll-confirmations",
        help="Iterate a batch of parsed emails, updating each matching draft",
    )
    pc.add_argument("--inbox-file", required=True, help="JSON list of Gmail payloads or eml file paths")
    pc.add_argument("--data-root", default="data")
    pc.add_argument("--window-days", type=int, default=None)

    # ----- Batch 4 Phase 9: retention + orphan cleanup -----
    prn = subparsers.add_parser(
        "prune-applications",
        help="Delete draft directories whose plan.prepared_at exceeds retention",
    )
    prn.add_argument("--older-than", type=int, required=True, help="Days threshold")
    prn.add_argument("--data-root", default="data")
    prn.add_argument("--dry-run", action="store_true")

    co = subparsers.add_parser(
        "cleanup-orphans",
        help="Remove orphaned checkpoints/ and attempts/ dirs (no plan.json/status.json sibling)",
    )
    co.add_argument("--data-root", default="data")
    co.add_argument("--confirm", action="store_true")

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
        # Attach resume variant selection if provided.
        if args.resume_variant:
            draft["selected_assets"]["selected_resume_content_id"] = args.resume_variant
            write_json(Path(args.output_dir) / f"{draft['draft_id']}.json", draft)
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

    if args.command == "recompute-tiers":
        from .application import recompute_tiers

        result = recompute_tiers(Path(args.applications_dir))
        print(json.dumps(result, indent=2))
        return 0

    # --- Generation commands ---
    if args.command == "generate-resume":
        from .generation import generate_resume_variants

        lead = read_json(Path(args.lead))
        profile = read_json(Path(args.profile))
        styles = [s.strip() for s in args.variants.split(",") if s.strip()]
        results = generate_resume_variants(lead, profile, styles, Path(args.output_dir))

        # CLI-orchestrated ATS check post-hook (generation.py does NOT import ats_check)
        if not args.skip_ats_check:
            from .ats_check import run_ats_check_with_recovery
            ats_check_dir = Path(args.output_dir).parent / "ats-checks"
            for record in results:
                record_path = Path(args.output_dir) / f"{record['content_id']}.json"
                run_ats_check_with_recovery(record_path, lead, ats_check_dir)

        print(json.dumps([r["content_id"] for r in results], indent=2))
        return 0

    if args.command == "generate-answers":
        from .generation import generate_answer_set

        lead = read_json(Path(args.lead))
        profile = read_json(Path(args.profile))
        if args.questions_file:
            questions = json.loads(Path(args.questions_file).read_text(encoding="utf-8"))
        elif args.questions:
            questions = [q.strip() for q in args.questions.split(",") if q.strip()]
        else:
            questions = []
        policy = {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})}
        result = generate_answer_set(lead, profile, questions, policy, Path(args.output_dir))
        print(json.dumps({"content_id": result["content_id"], "blocked": result.get("blocked", False)}, indent=2))
        return 0

    if args.command == "generate-cover-letter":
        from .generation import generate_cover_letter

        lead = read_json(Path(args.lead))
        profile = read_json(Path(args.profile))
        company = read_json(Path(args.company)) if args.company else None
        result = generate_cover_letter(
            lead, profile, company, Path(args.output_dir), lane=args.lane,
        )

        # CLI-orchestrated ATS check post-hook
        if not args.skip_ats_check:
            from .ats_check import run_ats_check_with_recovery
            ats_check_dir = Path(args.output_dir).parent / "ats-checks"
            record_path = Path(args.output_dir) / f"{result['content_id']}.json"
            run_ats_check_with_recovery(
                record_path, lead, ats_check_dir, company_research=company,
            )

        print(result["content_id"])
        return 0

    # --- Research commands ---
    if args.command == "research-company":
        from .research import research_company, research_company_from_lead

        if args.lead:
            lead = read_json(Path(args.lead))
            result = research_company_from_lead(lead, Path(args.output_dir))
        elif args.company:
            result = research_company(args.company, Path(args.output_dir))
        else:
            parser.error("research-company requires --lead or --company")
            return 2
        print(json.dumps({"company_id": result["company_id"]}, indent=2))
        return 0

    if args.command == "score-company-fit":
        from .research import score_company_fit

        company = read_json(Path(args.company))
        profile = read_json(Path(args.profile))
        result = score_company_fit(company, profile)
        # Update company file with score.
        company.update(result)
        write_json(Path(args.company), company)
        print(json.dumps(result, indent=2))
        return 0

    # --- Follow-up commands ---
    if args.command == "check-follow-ups":
        from .reminders import check_follow_ups

        result = check_follow_ups(Path(args.status_dir))
        if args.format == "text":
            for item in result:
                print(f"{item['lead_id']}: {item['follow_up_type']} (day {item['days_since']})")
        else:
            print(json.dumps(result, indent=2))
        return 0

    if args.command == "generate-follow-up":
        from .reminders import generate_follow_up_draft

        lead = read_json(Path(args.lead))
        profile = read_json(Path(args.profile))
        prefs = profile.get("preferences", {})
        fit = lead.get("fit_assessment", {})
        result = generate_follow_up_draft(
            lead_id=lead["lead_id"],
            candidate_name=prefs.get("candidate_name", "Candidate"),
            company_name=lead.get("company", ""),
            job_title=lead.get("title", ""),
            matched_skills=fit.get("matched_skills", [])[:5],
            follow_up_type="follow_up",
            output_dir=Path(args.output_dir),
        )
        print(result["path"])
        return 0

    if args.command == "export-pdf":
        from .pdf_export import PdfExportError, export_pdf, resolve_content_record_path

        try:
            content_record_path = resolve_content_record_path(
                args.content_record, args.content_id, Path(args.data_root)
            )
            record = export_pdf(content_record_path)
        except PdfExportError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({
            "status": "ok",
            "content_id": record.get("content_id"),
            "pdf_path": record.get("pdf_path"),
            "pdf_generated_at": record.get("pdf_generated_at"),
        }, indent=2))
        return 0

    if args.command == "ingest-url":
        from .ingestion import IngestionError, ingest_url, ingest_urls_file

        output_dir = Path(args.output_dir)
        if args.urls_file:
            # Batch mode — never raises; always prints {successes, failures}
            result = ingest_urls_file(Path(args.urls_file), output_dir, max_workers=args.max_workers)
            print(json.dumps({
                "status": "ok",
                "successes": [{"lead_id": l["lead_id"], "company": l.get("company"), "title": l.get("title")} for l in result["successes"]],
                "failures": result["failures"],
            }, indent=2))
            return 0 if not result["failures"] else 2
        try:
            html_override = None
            if args.html_file:
                html_override = Path(args.html_file).read_text(encoding="utf-8")
            lead = ingest_url(args.url, output_dir, html_override=html_override)
        except IngestionError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({
            "status": "ok",
            "lead_id": lead["lead_id"],
            "company": lead.get("company", ""),
            "title": lead.get("title", ""),
            "ingestion_method": lead.get("ingestion_method", ""),
        }, indent=2))
        return 0

    if args.command == "ats-check":
        from .ats_check import run_ats_check
        from .pdf_export import PdfExportError, resolve_content_record_path

        try:
            record_path = resolve_content_record_path(
                args.content_record, args.content_id, Path(args.data_root)
            )
        except PdfExportError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        record = read_json(record_path)
        lead = read_json(Path(args.lead)) if args.lead else None
        try:
            report = run_ats_check(record, lead, Path(args.output_dir), max_pages=args.target_pages)
        except ValueError as exc:
            print(json.dumps({
                "status": "error",
                "error_code": "check_failed",
                "message": str(exc),
            }, indent=2))
            return 2
        print(json.dumps(report, indent=2))
        return 0

    if args.command == "apps-dashboard":
        from .analytics import report_dashboard

        result = report_dashboard(
            Path(args.data_root),
            since=args.since,
            weeks=args.weeks,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "analyze-skills-gap":
        from .analytics import report_skills_gap

        profile_path = Path(args.profile)
        profile = read_json(profile_path) if profile_path.exists() else {}
        result = report_skills_gap(
            Path(args.data_root),
            profile,
            taxonomy_path=Path(args.taxonomy),
            excluded_path=Path(args.excluded),
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "analyze-rejections":
        from .analytics import report_rejection_patterns

        result = report_rejection_patterns(Path(args.data_root))
        print(json.dumps(result, indent=2))
        return 0

    # --- Batch 3: active job discovery ---
    if args.command == "discover-jobs":
        from .discovery import DiscoveryConfig, DiscoveryError, discover_jobs
        from .utils import StructuredError

        try:
            sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
            reset_cursor = None
            if args.reset_cursor:
                if "|" not in args.reset_cursor:
                    parser.error("--reset-cursor must be 'Company|source' or 'Company|*'")
                company, source = args.reset_cursor.split("|", 1)
                reset_cursor = (company, source)
            profile_path = Path(args.profile)
            profile = read_json(profile_path) if profile_path.exists() else {}
            scoring_config = load_yaml_file(Path(args.scoring_config), {})
            config = DiscoveryConfig(
                max_ingest=args.max_ingest,
                max_workers=args.max_workers,
                sources=sources,
                dry_run=args.dry_run,
                auto_score=not args.no_score,
                score_concurrency=args.score_concurrency,
                scoring_config=scoring_config,
                candidate_profile=profile if profile else None,
                reset_cursor=reset_cursor,
            )
            result = discover_jobs(
                watchlist_path=Path(args.watchlist),
                leads_dir=Path(args.leads_dir),
                discovery_root=Path(args.discovery_root),
                config=config,
            )
        except StructuredError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    if args.command == "discovery-state":
        from .discovery import BUCKETS, load_cursor

        discovery_root = Path(args.discovery_root)
        if args.last_run:
            history = discovery_root / "history"
            if not history.exists():
                print(json.dumps({"runs": []}, indent=2))
                return 0
            latest = max(history.glob("*.json"), default=None, key=lambda p: p.stat().st_mtime)
            if latest is None:
                print(json.dumps({"runs": []}, indent=2))
                return 0
            payload = read_json(latest)
            if args.bucket:
                if args.bucket not in BUCKETS:
                    parser.error(f"Unknown bucket: {args.bucket}")
                filtered = [o for o in payload.get("outcomes", []) if o.get("bucket") == args.bucket]
                payload = {"run": latest.name, "bucket": args.bucket, "outcomes": filtered}
            print(json.dumps(payload, indent=2))
            return 0
        cursor_path = discovery_root / "state.json"
        try:
            cursor = load_cursor(cursor_path)
        except Exception as exc:
            print(json.dumps({"status": "error", "error_code": "cursor_corrupt", "message": str(exc)}, indent=2))
            return 2
        entries = cursor.get("entries", {})
        filtered_items: list[dict] = []
        for key, value in entries.items():
            company, source = key.split("|", 1)
            if args.company and company != args.company:
                continue
            if args.source and source != args.source:
                continue
            filtered_items.append({
                "company": company, "source": source,
                **value,
            })
        print(json.dumps({"entries": filtered_items}, indent=2))
        return 0

    if args.command == "watchlist-show":
        from .watchlist import WatchlistValidationError, watchlist_show

        try:
            result = watchlist_show(Path(args.watchlist), args.company or None)
        except WatchlistValidationError as exc:
            print(json.dumps({"status": "error", "error_code": "watchlist_invalid", "message": str(exc)}, indent=2))
            return 2
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "watchlist-add":
        from .watchlist import WatchlistValidationError, validate_cli_string, watchlist_add

        try:
            for field_name in ("name", "greenhouse", "lever", "careers_url", "indeed_search_url", "notes"):
                validate_cli_string(getattr(args, field_name.replace("_", "_")) or "", field_name)
            entry = {"name": args.name}
            if args.greenhouse:
                entry["greenhouse"] = args.greenhouse
            if args.lever:
                entry["lever"] = args.lever
            if args.careers_url:
                entry["careers_url"] = args.careers_url
            if args.indeed_search_url:
                entry["indeed_search_url"] = args.indeed_search_url
            if args.notes:
                entry["notes"] = args.notes
            watchlist_add(Path(args.watchlist), entry, force=args.force)
        except WatchlistValidationError as exc:
            message = str(exc)
            if message == "watchlist_entry_exists":
                error_code = "watchlist_entry_exists"
            elif message == "watchlist_comments_present":
                error_code = "watchlist_comments_present"
            else:
                error_code = "watchlist_invalid"
            print(json.dumps({
                "status": "error", "error_code": error_code, "message": message,
                "remediation": "Use --force to overwrite a file with comments" if error_code == "watchlist_comments_present" else "",
            }, indent=2))
            return 2
        print(json.dumps({"status": "ok", "name": args.name}, indent=2))
        return 0

    if args.command == "watchlist-remove":
        from .watchlist import WatchlistValidationError, watchlist_remove

        try:
            watchlist_remove(Path(args.watchlist), args.name, force=args.force)
        except WatchlistValidationError as exc:
            print(json.dumps({"status": "error", "error_code": "watchlist_invalid", "message": str(exc)}, indent=2))
            return 2
        except FileNotFoundError:
            print(json.dumps({"status": "error", "error_code": "watchlist_invalid", "message": f"watchlist not found: {args.watchlist}"}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "name": args.name}, indent=2))
        return 0

    if args.command == "watchlist-validate":
        from .watchlist import watchlist_validate

        result = watchlist_validate(Path(args.watchlist))
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 2

    if args.command == "review-list":
        from .discovery import list_review_entries

        review_dir = Path(args.discovery_root) / "review"
        status = args.status if args.status else None
        entries = list_review_entries(review_dir, status=status)
        print(json.dumps({"entries": entries}, indent=2))
        return 0

    if args.command == "review-promote":
        from .discovery import promote_review_entry
        from .utils import StructuredError

        review_dir = Path(args.discovery_root) / "review"
        try:
            result = promote_review_entry(review_dir, args.entry_id, Path(args.leads_dir))
        except StructuredError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", **result}, indent=2))
        return 0

    if args.command == "review-dismiss":
        from .discovery import DiscoveryError, update_review_status

        review_dir = Path(args.discovery_root) / "review"
        try:
            update_review_status(review_dir, args.entry_id, "dismissed", reason=args.reason)
        except DiscoveryError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "entry_id": args.entry_id}, indent=2))
        return 0

    if args.command == "robots-cache-clear":
        cache_path = Path(args.discovery_root) / "robots_cache.json"
        if cache_path.exists():
            cache_path.unlink()
        print(json.dumps({"status": "ok", "cleared": str(cache_path)}, indent=2))
        return 0

    if args.command == "schemas-list":
        from .application import list_schemas

        print(json.dumps({"schemas": list_schemas()}, indent=2))
        return 0

    if args.command == "schemas-show":
        from .application import load_schema, PlanError

        try:
            body = load_schema(args.name)
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps(body, indent=2))
        return 0

    if args.command == "apply-preflight":
        from .application import run_preflight

        policy = {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})}
        report = run_preflight(policy)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2

    # --- Batch 4 Phase 2: answer bank CLIs ---
    if args.command == "answer-bank-list-pending":
        from .answer_bank import list_pending, write_pending_report
        from datetime import date as _date

        since = _date.fromisoformat(args.since) if args.since else None
        pending = list_pending(Path(args.bank), since=since)
        if args.report:
            write_pending_report(pending, Path(args.report))
        print(json.dumps({
            "status": "ok",
            "count": len(pending),
            "entries": pending,
            "report_path": args.report or None,
        }, indent=2))
        return 0

    if args.command == "answer-bank-list":
        from .answer_bank import list_entries
        from datetime import date as _date

        since = _date.fromisoformat(args.since) if args.since else None
        status = args.status or None
        entries = list_entries(Path(args.bank), status=status, since=since)
        print(json.dumps({"status": "ok", "count": len(entries), "entries": entries}, indent=2))
        return 0

    if args.command == "answer-bank-show":
        from .answer_bank import show_entry
        from .application import PlanError

        try:
            entry = show_entry(Path(args.bank), args.entry_id)
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "entry": entry}, indent=2))
        return 0

    if args.command == "answer-bank-validate":
        from .answer_bank import validate as validate_bank

        report = validate_bank(Path(args.bank))
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 2

    if args.command == "answer-bank-promote":
        from .answer_bank import promote, show_entry
        from .application import PlanError

        if args.dry_run:
            try:
                current = show_entry(Path(args.bank), args.entry_id)
            except PlanError as exc:
                print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
                return 2
            preview = {
                **current,
                "answer": args.answer,
                "source": "curated",
                "reviewed": True,
            }
            print(json.dumps({"status": "ok", "dry_run": True, "preview": preview}, indent=2))
            return 0
        try:
            entry = promote(
                args.entry_id,
                args.answer,
                Path(args.bank),
                notes=args.notes or None,
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "entry": entry}, indent=2))
        return 0

    if args.command == "answer-bank-deprecate":
        from .answer_bank import deprecate, show_entry
        from .application import PlanError

        if args.dry_run:
            try:
                current = show_entry(Path(args.bank), args.entry_id)
            except PlanError as exc:
                print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
                return 2
            preview = {**current, "deprecated": True, "notes": args.reason}
            print(json.dumps({"status": "ok", "dry_run": True, "preview": preview}, indent=2))
            return 0
        try:
            entry = deprecate(args.entry_id, args.reason, Path(args.bank))
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "entry": entry}, indent=2))
        return 0

    # --- Batch 4 Phase 4: application preparation + run ---
    if args.command == "prepare-application":
        from .application import PlanError, prepare_application

        lead = read_json(Path(args.lead))
        profile = read_json(Path(args.profile))
        policy = {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})}
        try:
            result = prepare_application(
                lead, profile, policy,
                output_root=Path(args.output_root),
                force=args.force,
                data_root=Path(args.data_root),
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        payload = {
            "status": "ok",
            "draft_id": result.draft_id,
            "draft_dir": str(result.draft_dir),
            "tier": result.tier,
            "tier_rationale": result.tier_rationale,
            "surface": result.surface,
        }
        if args.dry_run:
            # Dry-run emits the plan to stdout without leaving artifacts
            # — but prepare_application has already written. Best we can do
            # without a deeper rework is delete on --dry-run exit.
            plan = read_json(result.draft_dir / "plan.json")
            payload["plan"] = plan
            payload["dry_run"] = True
            import shutil
            shutil.rmtree(result.draft_dir, ignore_errors=True)
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "apply-posting":
        from .application import PlanError, apply_posting

        try:
            bundle = apply_posting(
                args.draft_id,
                dry_run=args.dry_run,
                data_root=Path(args.data_root),
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps(bundle, indent=2))
        return 0

    if args.command == "record-attempt":
        from .application import ApplicationError, PlanError, record_attempt

        payload = read_json(Path(args.attempt_file))
        if args.dry_run:
            # Validate shape but do not write.
            from .application import _validate_attempt_shape  # noqa: PLC2701
            try:
                _validate_attempt_shape(payload)
            except PlanError as exc:
                print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
                return 2
            print(json.dumps({"status": "ok", "dry_run": True, "payload": payload}, indent=2))
            return 0
        try:
            result = record_attempt(
                args.draft_id, payload, data_root=Path(args.data_root)
            )
        except (ApplicationError, PlanError) as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "attempt": result}, indent=2))
        return 0

    if args.command == "apply-status":
        from .application import PlanError, apply_status

        try:
            status = apply_status(args.draft_id, data_root=Path(args.data_root))
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps(status, indent=2))
        return 0

    if args.command == "reconcile-applications":
        from .application import reconcile_stale_attempts

        policy = {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})}
        reconciled = reconcile_stale_attempts(
            policy,
            current_batch_id=args.current_batch_id or None,
            data_root=Path(args.data_root),
        )
        print(json.dumps({"status": "ok", "count": len(reconciled), "reconciled": reconciled}, indent=2))
        return 0

    if args.command == "draft-list":
        from .application import list_drafts

        drafts = list_drafts(
            tier=args.tier or None,
            status=args.status or None,
            source=args.source or None,
            data_root=Path(args.data_root),
        )
        print(json.dumps({"status": "ok", "count": len(drafts), "drafts": drafts}, indent=2))
        return 0

    if args.command == "refresh-application":
        from .application import PlanError, refresh_application

        profile = read_json(Path(args.profile))
        try:
            plan = refresh_application(
                args.draft_id, profile, data_root=Path(args.data_root)
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({
            "status": "ok",
            "draft_id": args.draft_id,
            "profile_snapshot": plan["profile_snapshot"],
        }, indent=2))
        return 0

    if args.command == "checkpoint-update":
        from .application import PlanError, checkpoint_update

        try:
            entry = checkpoint_update(
                args.draft_id,
                args.attempt_id,
                args.checkpoint,
                screenshot_path=args.screenshot or None,
                data_root=Path(args.data_root),
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "attempt_summary": entry}, indent=2))
        return 0

    if args.command == "mark-applied-externally":
        from .application import PlanError, mark_applied_externally

        if args.dry_run:
            print(json.dumps({"status": "ok", "dry_run": True, "lead_id": args.lead_id}, indent=2))
            return 0
        try:
            status = mark_applied_externally(
                args.lead_id,
                applied_at=args.applied_at or None,
                note=args.note,
                data_root=Path(args.data_root),
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "lead_id": args.lead_id, "lifecycle_state": status["lifecycle_state"]}, indent=2))
        return 0

    if args.command == "withdraw-application":
        from .application import PlanError, withdraw_application

        if args.dry_run:
            print(json.dumps({"status": "ok", "dry_run": True, "draft_id": args.draft_id}, indent=2))
            return 0
        try:
            status = withdraw_application(
                args.draft_id, args.reason, data_root=Path(args.data_root)
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "draft_id": args.draft_id, "lifecycle_state": status["lifecycle_state"]}, indent=2))
        return 0

    if args.command == "reopen-application":
        from .application import PlanError, reopen_application

        if args.dry_run:
            print(json.dumps({"status": "ok", "dry_run": True, "draft_id": args.draft_id}, indent=2))
            return 0
        try:
            status = reopen_application(args.draft_id, data_root=Path(args.data_root))
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "draft_id": args.draft_id, "lifecycle_state": status["lifecycle_state"]}, indent=2))
        return 0

    # --- Batch 4 Phase 7: batch orchestration ---
    if args.command == "apply-batch":
        from .application import PlanError, apply_batch

        policy = {**DEFAULT_RUNTIME_POLICY, **load_yaml_file(Path(args.runtime_config), {})}
        profile = read_json(Path(args.profile))
        try:
            result = apply_batch(
                top=args.top,
                score_floor=args.floor,
                source=args.source or None,
                dry_run=args.dry_run,
                runtime_policy=policy,
                candidate_profile=profile,
                data_root=Path(args.data_root),
                leads_dir=Path(args.leads_dir),
            )
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", **result}, indent=2))
        return 0

    if args.command == "batch-list":
        from .application import list_batches
        from datetime import datetime as _dt

        since = None
        if args.since:
            try:
                since = _dt.fromisoformat(args.since)
            except ValueError:
                parser.error(f"Invalid --since date: {args.since}")
                return 2
        batches = list_batches(
            active=args.active, since=since, data_root=Path(args.data_root)
        )
        print(json.dumps({"status": "ok", "count": len(batches), "batches": batches}, indent=2))
        return 0

    if args.command == "batch-status":
        from .application import PlanError, batch_status

        try:
            payload = batch_status(args.batch_id, data_root=Path(args.data_root))
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", "batch_id": args.batch_id, **payload}, indent=2))
        return 0

    if args.command == "batch-cancel":
        from .application import PlanError, batch_cancel

        if args.dry_run:
            print(json.dumps({"status": "ok", "dry_run": True, "batch_id": args.batch_id}, indent=2))
            return 0
        try:
            payload = batch_cancel(args.batch_id, data_root=Path(args.data_root))
        except PlanError as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({"status": "ok", **payload}, indent=2))
        return 0

    # --- Batch 4 Phase 8: Gmail-driven confirmation ---
    if args.command == "ingest-confirmation":
        from .application import ApplicationError, PlanError
        from .confirmation import ingest_confirmation

        msg_path = Path(args.gmail_message_file)
        raw_bytes: bytes | None = None
        payload: dict | None = None
        if msg_path.suffix.lower() == ".json":
            payload = read_json(msg_path)
        else:
            raw_bytes = msg_path.read_bytes()

        if args.dry_run:
            from .confirmation import parse_email, parse_email_dict
            parsed = parse_email_dict(payload) if payload is not None else parse_email(raw_bytes)
            print(json.dumps({
                "status": "ok",
                "dry_run": True,
                "parsed": {
                    "sender": parsed.sender,
                    "message_id": parsed.message_id,
                    "subject": parsed.subject,
                    "event_type": parsed.event_type,
                    "indeed_jk": parsed.indeed_jk,
                    "posting_url": parsed.posting_url,
                },
            }, indent=2))
            return 0

        try:
            updated = ingest_confirmation(
                raw_bytes=raw_bytes,
                payload=payload,
                draft_id=args.draft_id or None,
                data_root=Path(args.data_root),
            )
        except (ApplicationError, PlanError) as exc:
            print(json.dumps({"status": "error", **exc.to_dict()}, indent=2))
            return 2
        print(json.dumps({
            "status": "ok",
            "draft_id": updated.get("draft_id") or args.draft_id,
            "lifecycle_state": updated.get("lifecycle_state"),
        }, indent=2))
        return 0

    if args.command == "poll-confirmations":
        from .confirmation import (
            parse_email,
            parse_email_dict,
            poll_confirmations,
        )

        inbox = read_json(Path(args.inbox_file))
        if not isinstance(inbox, list):
            print(json.dumps({
                "status": "error",
                "error_code": "plan_schema_invalid",
                "message": "inbox-file must be a JSON list",
            }, indent=2))
            return 2
        parsed_list = []
        for item in inbox:
            if isinstance(item, dict):
                parsed_list.append(parse_email_dict(item))
            elif isinstance(item, str):
                parsed_list.append(parse_email(Path(item).read_bytes()))
        rollup = poll_confirmations(parsed_list, data_root=Path(args.data_root))
        print(json.dumps({"status": "ok", **rollup}, indent=2))
        return 0

    # --- Batch 4 Phase 9: retention + cleanup ---
    if args.command == "prune-applications":
        from .application import prune_applications

        result = prune_applications(
            older_than_days=args.older_than,
            dry_run=args.dry_run,
            data_root=Path(args.data_root),
        )
        print(json.dumps({"status": "ok", **result}, indent=2))
        return 0

    if args.command == "cleanup-orphans":
        from .application import cleanup_orphans

        result = cleanup_orphans(
            confirm=args.confirm,
            data_root=Path(args.data_root),
        )
        print(json.dumps({"status": "ok", **result}, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
