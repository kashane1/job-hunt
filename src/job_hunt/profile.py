"""Profile-side helpers extracted from core.py to break import cycles.

Phase 1b (batch 4) splits the application pipeline across three modules:
``core.py`` (CLI dispatcher + legacy logic), ``profile.py`` (this file —
candidate profile and draft construction), and ``application.py`` (Phase 4 —
browser-adjacent orchestration). ``application.py`` imports from ``profile.py``
but MUST NOT import ``core.py``; keeping the split enforced here means Phase 4
cannot accidentally reintroduce a cycle.

Only code that is (a) already stable and (b) needed by Phase 4 moves here.
Inference, scoring, normalization, and CLI wiring stay in ``core.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

from .utils import ensure_dir, now_iso, tokens, write_json


COMMON_QUESTION_TEMPLATES: Final = [
    "Why are you interested in this role?",
    "What makes you a strong fit for this position?",
    "Are you aligned with the work model and location expectations?",
]


# =============================================================================
# Profile completeness
# =============================================================================

COMPLETENESS_CHECKS: Final = [
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


# =============================================================================
# Application draft construction (Phase 4 build_application_draft dependency)
# =============================================================================

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
