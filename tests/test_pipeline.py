from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.core import (
    build_application_draft,
    check_profile_completeness,
    extract_lead,
    normalize_profile,
    score_lead,
    summarize_run,
    write_application_report,
)
from job_hunt.schema_checks import validate


class PipelineTest(unittest.TestCase):
    def test_end_to_end_artifacts_are_generated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_raw = root / "profile" / "raw"
            profile_raw.mkdir(parents=True)
            normalized = root / "profile" / "normalized"
            leads_dir = root / "data" / "leads"
            applications_dir = root / "data" / "applications"
            runs_dir = root / "data" / "runs"
            reports_dir = root / "docs" / "reports"

            (profile_raw / "resume.md").write_text(
                """---
document_type: resume
title: Staff Platform Resume
tags:
  - python
  - platform
  - backend
---
# Staff Platform Resume

- Built internal platform automation for deployment and operations
- Led backend work across Python, Postgres, and AWS
Contact: person@example.com
""",
                encoding="utf-8",
            )
            (profile_raw / "qa.md").write_text(
                """---
document_type: question_bank
title: Common Answers
---
Q: Why do you want this role?
A: I like work that blends platform engineering, automation, and product impact.
""",
                encoding="utf-8",
            )
            (profile_raw / "preferences.md").write_text(
                """---
document_type: preferences
title: Preferences
target_titles:
  - Staff Platform Engineer
preferred_locations:
  - Remote
remote_preference: remote
excluded_keywords:
  - clearance
---
""",
                encoding="utf-8",
            )

            profile = normalize_profile(
                root / "profile",
                normalized,
                {"skill_keywords": ["python", "platform", "backend", "aws", "postgres"]},
            )
            document_audit = json.loads((normalized / "document-audit.json").read_text(encoding="utf-8"))
            audit_report = (root / "docs" / "reports" / "profile-document-audit.md").read_text(encoding="utf-8")

            lead_source = root / "lead.md"
            lead_source.write_text(
                """---
source: greenhouse
company: ExampleCo
title: Staff Platform Engineer
location: Remote
application_url: https://example.com/jobs/123
---
# Staff Platform Engineer

## Requirements
- Python
- Platform
- AWS
- Postgres
""",
                encoding="utf-8",
            )
            lead = extract_lead(lead_source, leads_dir)
            lead = score_lead(
                lead,
                profile,
                {
                    "title_match_weight": 20,
                    "skills_match_weight": 35,
                    "seniority_match_weight": 10,
                    "location_match_weight": 10,
                    "domain_match_weight": 10,
                    "compensation_match_weight": 5,
                    "negative_keyword_penalty_weight": 10,
                    "strong_yes_threshold": 75,
                    "maybe_threshold": 55,
                    "negative_keywords": ["clearance"],
                },
            )
            draft = build_application_draft(
                lead,
                profile,
                {
                    "approval_required_before_submit": True,
                    "approval_required_before_account_creation": True,
                    "browser_tabs_soft_limit": 10,
                    "browser_tabs_hard_limit": 15,
                    "stop_if_required_fact_missing": True,
                    "redact_secrets_in_artifacts": True,
                },
                applications_dir,
            )
            draft["approval"]["final_submit"]["approved"] = True
            draft["approval"]["account_creation"]["approved"] = True
            (applications_dir / f"{draft['draft_id']}.json").write_text(
                json.dumps(draft, indent=2), encoding="utf-8"
            )

            report = write_application_report(
                draft,
                {
                    "attempted": True,
                    "confirmed_submitted": True,
                    "account_action": "reused",
                    "blocked_reason": "",
                    "final_url": "https://example.com/thanks",
                    "password": "super-secret",
                    "tab_metrics": {
                        "opened": 3,
                        "peak_open_tabs": 3,
                        "closed_for_budget": 1,
                        "hard_limit_hit": False,
                    },
                },
                {
                    "browser_tabs_soft_limit": 10,
                    "browser_tabs_hard_limit": 15,
                    "redact_secrets_in_artifacts": True,
                },
                applications_dir,
                reports_dir,
            )
            summary = summarize_run(leads_dir, applications_dir, runs_dir, reports_dir)

            schemas_root = Path(__file__).resolve().parents[1] / "schemas"
            validate(profile, json.loads((schemas_root / "candidate-profile.schema.json").read_text()))
            validate(lead, json.loads((schemas_root / "lead.schema.json").read_text()))
            validate(draft, json.loads((schemas_root / "application-draft.schema.json").read_text()))
            validate(report, json.loads((schemas_root / "application-report.schema.json").read_text()))
            validate(summary, json.loads((schemas_root / "run-summary.schema.json").read_text()))
            self.assertEqual(document_audit["supported_document_count"], 3)
            self.assertIn("Highest-Value Documents", audit_report)
            self.assertTrue((normalized / "documents").exists())
            self.assertTrue(report["submission"]["final_submit_approval_required"])
            self.assertTrue(report["submission"]["final_submit_approval_obtained"])
            self.assertTrue(report["submission"]["account_creation_approval_required"])
            self.assertTrue(report["submission"]["account_creation_approval_obtained"])
            self.assertEqual(report["attempt"]["password"], "[REDACTED]")
            self.assertIn("password", report["redaction"]["fields_redacted"])
            self.assertEqual(report["browser_metrics"]["peak_open_tabs"], 3)
            self.assertEqual(summary["quality_metrics"]["confirmed_submissions"], 1)

    def test_normalize_profile_prefers_candidate_contact_and_extracts_freeform_answers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_raw = root / "profile" / "raw"
            normalized = root / "profile" / "normalized"
            profile_raw.mkdir(parents=True)

            (profile_raw / "resume.txt").write_text(
                """Kashane Sakhakorn
ksakhakorn@gmail.com | (818) 282-3532 | https://www.linkedin.com/in/kashanesakhakorn/

Technical Skills
Languages & Frameworks: TypeScript, JavaScript, PHP, Postgres, MySQL, React
Tools & Platforms: AWS, Docker, Kafka, Redis
Professional Experience
* Built APIs in TypeScript and React
""",
                encoding="utf-8",
            )
            (profile_raw / "Drafts.txt").write_text(
                """Cover Letter
Kashane Sakhakorn
ksakhakorn@gmail.com
(818) 282-3532

Dear Hiring Manager,
This is a tailored cover letter.

What is your expected annual cash compensation?
I am targeting $140,000 annually and am open to discussing total compensation.

What are you looking for in your next position?
I want a remote-friendly engineering role where I can build meaningful products with TypeScript and React.

https://job-boards.greenhouse.io/example/jobs/1234567
2025-06-09
""",
                encoding="utf-8",
            )
            (profile_raw / "work-notes.txt").write_text(
                """Work Notes 2026
debug links http://localhost:8080/inventory
error address dev@example.com
random date 2025-06-09
""",
                encoding="utf-8",
            )

            profile = normalize_profile(
                root / "profile",
                normalized,
                {"skill_keywords": ["typescript", "javascript", "php", "postgres", "mysql", "react", "aws", "docker", "kafka", "redis"]},
            )

            self.assertEqual(profile["contact"]["emails"], ["ksakhakorn@gmail.com"])
            self.assertEqual(profile["contact"]["phones"], ["(818) 282-3532"])
            self.assertEqual(
                profile["contact"]["links"],
                ["https://www.linkedin.com/in/kashanesakhakorn/"],
            )
            compensation_answers = [
                item for item in profile["question_bank"]
                if item["question"] == "What is your expected annual cash compensation?"
            ]
            self.assertEqual(len(compensation_answers), 1)
            self.assertEqual(
                compensation_answers[0]["answer"],
                "I am targeting $140,000 annually and am open to discussing total compensation.",
            )
            self.assertEqual(compensation_answers[0]["provenance"], "grounded")
            skill_names = {item["name"] for item in profile["skills"]}
            self.assertTrue({"typescript", "javascript", "php", "postgres", "mysql", "react", "aws", "docker", "kafka", "redis"} <= skill_names)
            self.assertNotIn("ai", skill_names)
            self.assertEqual(profile["preferences"]["minimum_compensation"], "$140,000")
            self.assertEqual(profile["preferences"]["remote_preference"], "remote")
            self.assertEqual(profile["preferences"]["preferred_locations"], ["Remote"])

    def test_account_creation_penalizes_missing_approval_and_blocks_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            applications_dir = root / "data" / "applications"
            reports_dir = root / "docs" / "reports"

            draft = {
                "draft_id": "example-draft",
                "lead_id": "example-lead",
                "created_at": "2026-04-15T00:00:00+00:00",
                "approval": {
                    "final_submit": {"required": True, "approved": False, "reviewer": ""},
                    "account_creation": {"required": True, "approved": False, "reviewer": ""},
                },
                "prepared_answers": [
                    {
                        "question": "Why do you want this role?",
                        "answer": "Because it aligns with my background.",
                        "provenance": "synthesized",
                        "confidence": 0.82,
                        "needs_review": False,
                        "source_document_ids": ["resume-1"],
                    }
                ],
                "missing_facts": [],
            }

            report = write_application_report(
                draft,
                {
                    "attempted": True,
                    "confirmed_submitted": False,
                    "account_action": "created",
                    "blocked_reason": "approval_missing",
                    "tab_metrics": {"peak_open_tabs": 2},
                },
                {
                    "browser_tabs_soft_limit": 10,
                    "browser_tabs_hard_limit": 15,
                    "redact_secrets_in_artifacts": True,
                },
                applications_dir,
                reports_dir,
            )

            self.assertEqual(report["submission"]["status"], "blocked")
            self.assertFalse(report["submission"]["account_creation_approval_obtained"])
            self.assertEqual(report["blockers"], ["approval_missing"])
            self.assertLess(report["quality"]["application_quality_score"], 100)


    def test_profile_completeness_check(self) -> None:
        complete_profile = {
            "contact": {
                "emails": ["test@example.com"],
                "phones": ["(555) 123-4567"],
                "links": ["https://www.linkedin.com/in/testuser/"],
            },
            "documents": [
                {"document_id": "resume-1", "document_type": "resume", "path": "r.md", "title": "Resume", "source_excerpt": ""},
                {"document_id": "proj-1", "document_type": "project_note", "path": "p.md", "title": "Project", "source_excerpt": ""},
                {"document_id": "pref-1", "document_type": "preferences", "path": "pref.md", "title": "Preferences", "source_excerpt": ""},
            ],
            "skills": [{"name": f"skill-{i}", "source_document_ids": ["resume-1"]} for i in range(6)],
            "question_bank": [
                {"question": f"Q{i}?", "answer": "A" * 30, "provenance": "grounded", "source_document_ids": ["resume-1"]}
                for i in range(4)
            ],
            "experience_highlights": [
                {"summary": "Improved latency by 50% across the platform", "source_document_ids": ["resume-1"]},
                {"summary": "Migrated 10+ years of data with 100% integrity", "source_document_ids": ["resume-1"]},
                {"summary": "Drove revenue to over $10M annually", "source_document_ids": ["resume-1"]},
            ],
            "preferences": {
                "target_titles": ["Senior Engineer"],
                "preferred_locations": ["Remote"],
                "remote_preference": "remote",
                "excluded_keywords": ["clearance"],
                "minimum_compensation": "$140,000",
                "work_authorization": "us_citizen",
            },
        }
        result = check_profile_completeness(complete_profile, {})
        self.assertEqual(result["completeness_score"], 100)
        self.assertEqual(result["readiness"], "ready")
        self.assertEqual(result["missing"], [])

        empty_profile = {
            "contact": {"emails": [], "phones": [], "links": []},
            "documents": [],
            "skills": [],
            "question_bank": [],
            "experience_highlights": [],
            "preferences": {},
        }
        result = check_profile_completeness(empty_profile, {})
        self.assertLess(result["completeness_score"], 50)
        self.assertEqual(result["readiness"], "not_ready")
        self.assertGreater(len(result["missing"]), 5)


if __name__ == "__main__":
    unittest.main()
