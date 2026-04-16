"""ATS compatibility checker for generated resumes and cover letters.

Runs at the CLI layer AFTER generation (not inside generation.py — architecture
review flagged that coupling). Two-phase crash-safe write updates the content
record's `ats_check` field: pending → passed/warnings/errors/check_failed.

Design per batch 2 Phase 3:
- check_resume validates format, sections, length, keyword COVERAGE (fraction
  of lead keywords in content) and keyword DENSITY (fraction of content that
  is keywords — stuffing check).
- check_cover_letter validates opening, length, basic structure.
- run_ats_check dispatches by content_type, writes report file, returns dict.
- run_ats_check_with_recovery wraps the two-phase write: pending → result.
  Crash between phases leaves ats_check.status == "pending"; check-integrity
  surfaces for re-run.

Internal module — raises ValueError directly per batch 1 convention
(structured errors are for I/O/CLI boundaries like ingestion, pdf_export).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from .generation import generation_tokens
from .utils import now_iso, read_json, write_json

# Resume structure — derived from render_resume_markdown output
REQUIRED_RESUME_SECTIONS: Final = ("Technical Skills", "Professional Experience", "Education")
REQUIRED_COVER_LETTER_OPENING: Final = re.compile(r"Dear\s+(Hiring Manager|\w+)", re.I)

# Length thresholds (2026 industry norms per research)
RESUME_MAX_PAGES_DEFAULT: Final = 1  # <5 YOE engineers — single page
COVER_LETTER_MAX_WORDS: Final = 400
RESUME_MIN_WORDS: Final = 200
RESUME_TARGET_WORDS_MIN: Final = 475  # research sweet spot: 475-600 correlates
RESUME_TARGET_WORDS_MAX: Final = 600  # with ~2x interview rate
WORDS_PER_PAGE_ESTIMATE: Final = 475

# Keyword COVERAGE: fraction of lead keywords that appear in content. Target 60-80%.
KEYWORD_COVERAGE_WARN_THRESHOLD: Final = 0.60
KEYWORD_COVERAGE_ERROR_THRESHOLD: Final = 0.30

# Keyword DENSITY: fraction of content tokens that are lead keywords.
# >5% reads as stuffing to several ATS systems.
KEYWORD_DENSITY_STUFFING_THRESHOLD: Final = 0.05


def _jaccard_coverage(lead_keywords: set[str], content_token_set: set[str]) -> float:
    """Coverage = matched / total_lead_keywords. NOT Jaccard.
    Jaccard would penalize a resume for having non-keyword tokens, which is wrong.
    We want: 'what fraction of the required keywords are actually present?'"""
    if not lead_keywords:
        return 0.0
    return len(lead_keywords & content_token_set) / len(lead_keywords)


def check_resume(
    md_text: str,
    lead: dict | None,
    max_pages: int = RESUME_MAX_PAGES_DEFAULT,
) -> dict:
    """Validate a resume markdown. Returns {errors, warnings, metrics}.

    Errors are blocking (missing required section, resume too short).
    Warnings are advisory (off target word count, low keyword coverage).
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    metrics: dict = {}

    words = md_text.split()
    metrics["word_count"] = len(words)
    metrics["page_estimate"] = round(len(words) / WORDS_PER_PAGE_ESTIMATE, 1)

    for section in REQUIRED_RESUME_SECTIONS:
        if section not in md_text:
            errors.append({
                "code": "missing_required_section",
                "message": f"Resume is missing required section: {section!r}",
                "location": "document",
            })
    if metrics["page_estimate"] > max_pages:
        warnings.append({
            "code": "resume_too_long",
            "message": f"Resume is ~{metrics['page_estimate']} pages; target {max_pages} page(s).",
        })
    if metrics["word_count"] < RESUME_MIN_WORDS:
        errors.append({
            "code": "resume_too_short",
            "message": f"Resume has {metrics['word_count']} words; minimum {RESUME_MIN_WORDS}.",
        })
    elif not (RESUME_TARGET_WORDS_MIN <= metrics["word_count"] <= RESUME_TARGET_WORDS_MAX):
        warnings.append({
            "code": "resume_word_count_off_target",
            "message": (
                f"{metrics['word_count']} words; industry sweet spot is "
                f"{RESUME_TARGET_WORDS_MIN}-{RESUME_TARGET_WORDS_MAX}."
            ),
        })

    if lead is not None:
        lead_keywords = set(
            lead.get("normalized_requirements", {}).get("keywords", [])
        )
        content_tokens = generation_tokens(md_text)
        content_token_set = set(content_tokens)

        matched = sorted(lead_keywords & content_token_set)
        missing = sorted(
            kw for kw in lead_keywords
            if kw not in content_token_set and len(kw) > 2
        )

        # Coverage: fraction of lead keywords that appear in content
        coverage = _jaccard_coverage(lead_keywords, content_token_set)
        # Density: fraction of content that IS lead keywords (stuffing check)
        matched_token_count = sum(1 for t in content_tokens if t in lead_keywords)
        density = matched_token_count / max(len(content_tokens), 1)

        metrics["keyword_coverage"] = round(coverage, 3)
        metrics["keyword_density"] = round(density, 3)
        metrics["matched_keywords"] = matched
        metrics["missing_keywords"] = missing[:20]

        if coverage < KEYWORD_COVERAGE_ERROR_THRESHOLD:
            errors.append({
                "code": "low_keyword_coverage",
                "message": (
                    f"Only {round(coverage * 100, 1)}% of lead keywords match the resume; "
                    f"error threshold {round(KEYWORD_COVERAGE_ERROR_THRESHOLD * 100)}%."
                ),
            })
        elif coverage < KEYWORD_COVERAGE_WARN_THRESHOLD:
            warnings.append({
                "code": "keyword_coverage_below_target",
                "message": (
                    f"Keyword coverage {round(coverage * 100, 1)}%; "
                    f"target ≥{round(KEYWORD_COVERAGE_WARN_THRESHOLD * 100)}%."
                ),
            })
        if density > KEYWORD_DENSITY_STUFFING_THRESHOLD:
            errors.append({
                "code": "keyword_stuffing",
                "message": (
                    f"Keyword density {round(density * 100, 1)}% exceeds "
                    f"{round(KEYWORD_DENSITY_STUFFING_THRESHOLD * 100)}% — reads as stuffing."
                ),
            })

    return {"errors": errors, "warnings": warnings, "metrics": metrics}


def check_cover_letter(md_text: str, lead: dict | None) -> dict:
    """Validate a cover letter markdown."""
    errors: list[dict] = []
    warnings: list[dict] = []
    metrics: dict = {}

    words = md_text.split()
    metrics["word_count"] = len(words)

    if not REQUIRED_COVER_LETTER_OPENING.search(md_text):
        warnings.append({
            "code": "missing_opening_salutation",
            "message": "Cover letter does not start with a 'Dear ...' salutation.",
        })
    if metrics["word_count"] > COVER_LETTER_MAX_WORDS:
        warnings.append({
            "code": "cover_letter_too_long",
            "message": (
                f"Cover letter has {metrics['word_count']} words; target under "
                f"{COVER_LETTER_MAX_WORDS}."
            ),
        })

    if lead is not None:
        lead_keywords = set(
            lead.get("normalized_requirements", {}).get("keywords", [])
        )
        content_tokens = generation_tokens(md_text)
        content_token_set = set(content_tokens)
        matched = sorted(lead_keywords & content_token_set)
        metrics["keyword_coverage"] = round(
            _jaccard_coverage(lead_keywords, content_token_set), 3
        )
        metrics["matched_keywords"] = matched

    return {"errors": errors, "warnings": warnings, "metrics": metrics}


def run_ats_check(
    content_record: dict,
    lead: dict | None,
    output_dir: Path,
    max_pages: int = RESUME_MAX_PAGES_DEFAULT,
) -> dict:
    """Run the appropriate check for content_type, write report to output_dir,
    return the full report dict (matching ats-check-report.schema.json)."""
    content_type = content_record.get("content_type")
    output_path_str = content_record.get("output_path", "")
    if not output_path_str:
        raise ValueError(f"content record has no output_path: {content_record.get('content_id')}")
    md_path = Path(output_path_str)
    if not md_path.exists():
        raise ValueError(f"markdown source not found: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    if content_type == "resume":
        result = check_resume(md_text, lead, max_pages=max_pages)
    elif content_type == "cover_letter":
        result = check_cover_letter(md_text, lead)
    else:
        # Answer sets etc. have no per-document checks today — report passed.
        result = {"errors": [], "warnings": [], "metrics": {"word_count": len(md_text.split())}}

    status = (
        "errors" if result["errors"]
        else ("warnings" if result["warnings"] else "passed")
    )
    content_id = content_record.get("content_id", "unknown")
    report = {
        "report_id": f"{content_id}-check",
        "content_id": content_id,
        "lead_id": content_record.get("lead_id", ""),
        "checked_at": now_iso(),
        "status": status,
        "errors": result["errors"],
        "warnings": result["warnings"],
        "metrics": result["metrics"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / f"{report['report_id']}.json", report)
    return report


def run_ats_check_with_recovery(
    record_path: Path,
    lead: dict | None,
    ats_check_dir: Path,
    max_pages: int = RESUME_MAX_PAGES_DEFAULT,
) -> dict:
    """Crash-safe two-phase update of the content record's ats_check field.

    Phase 1: mark ats_check.status = "pending" (atomic write_json).
    Phase 2: run the check (may raise; may take seconds).
    Phase 3: patch record with result or "check_failed" status (atomic write_json).

    NOT atomic at the sequence level — a crash between phases 1 and 3 leaves
    the record in "pending" state. check_integrity surfaces these for re-run.
    """
    record = read_json(record_path)
    record["ats_check"] = {"status": "pending", "checked_at": now_iso()}
    write_json(record_path, record)  # atomic per-call via utils.write_json

    try:
        report = run_ats_check(record, lead, ats_check_dir, max_pages=max_pages)
        record["ats_check"] = {
            "status": report["status"],
            "report_path": str(ats_check_dir / f"{report['report_id']}.json"),
            "checked_at": report["checked_at"],
        }
    except Exception as exc:
        record["ats_check"] = {
            "status": "check_failed",
            "error": str(exc),
            "checked_at": now_iso(),
        }
    write_json(record_path, record)  # atomic per-call
    return record
