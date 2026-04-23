"""Glassdoor-origin end-to-end pipeline tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.application import (
    apply_posting,
    apply_status,
    checkpoint_update,
    prepare_application,
    record_attempt,
)
from job_hunt.boards.registry import resolve_application_target
from job_hunt.core import extract_lead
from job_hunt.utils import write_json


PROFILE = {
    "contact": {
        "emails": ["cand@example.com"],
        "phones": ["(555) 555-0100"],
        "links": ["https://www.linkedin.com/in/cand/"],
    },
    "documents": [
        {"document_id": "d1", "document_type": "resume", "title": "Resume", "source_excerpt": ""},
        {"document_id": "d2", "document_type": "preferences", "title": "Prefs", "source_excerpt": ""},
    ],
    "skills": [
        {"name": "python", "source_document_ids": ["d1"]},
        {"name": "aws", "source_document_ids": ["d1"]},
    ],
    "experience_highlights": [
        {"summary": "Shipped backend and platform systems from 2021 to 2026", "source_document_ids": ["d1"]},
    ],
    "question_bank": [],
    "preferences": {
        "target_titles": ["Senior Software Engineer"],
        "preferred_locations": ["Remote"],
        "remote_preference": "remote",
        "excluded_keywords": [],
        "work_authorization": "US Citizen",
        "sponsorship_required": False,
        "minimum_compensation": "$150,000",
    },
}

POLICY = {
    "approval_required_before_submit": True,
    "approval_required_before_account_creation": True,
    "apply_policy": {
        "auto_submit_tiers": [],
        "stale_attempt_threshold_minutes": 45,
    },
}


def _seed_bank(data_root: Path) -> None:
    entries = [
        ("work_auth_yes", "are you legally authorized to work in the united states", "Yes", "yes_no"),
        ("sponsorship_no", "will you now or in the future require sponsorship for employment visa status", "No", "yes_no"),
        ("remote_yes", "are you willing to work remotely", "Yes", "yes_no"),
        ("start_date", "when can you start", "Two weeks", "text"),
        ("min_salary", "what is your minimum salary expectation", "$150,000", "text"),
        ("linkedin", "linkedin url", "https://www.linkedin.com/in/cand/", "text"),
        ("why_role", "why are you interested in this role", "Strong fit with my platform background.", "text"),
    ]
    write_json(data_root / "answer-bank.json", {
        "schema_version": 1,
        "entries": [
            {
                "entry_id": entry_id,
                "canonical_question": question,
                "answer": answer,
                "answer_format": answer_format,
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            }
            for entry_id, question, answer, answer_format in entries
        ],
    })


class GlassdoorPipelineTest(unittest.TestCase):
    def test_glassdoor_hosted_flow_keeps_human_submit_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            leads_dir = data_root / "leads"
            source = data_root / "glassdoor-manual.json"
            _seed_bank(data_root)
            source.write_text(json.dumps({
                "origin_board": "glassdoor",
                "source": "glassdoor_manual",
                "company": "ExampleCo",
                "title": "Senior Software Engineer",
                "location": "Remote",
                "application_url": "https://www.glassdoor.com/job-listing/example-role",
                "canonical_url": "https://www.glassdoor.com/job-listing/example-role",
                "posting_url": "https://www.glassdoor.com/job-listing/example-role",
                "raw_description": "Apply on Glassdoor.\nRequirements: Python, AWS.",
            }), encoding="utf-8")

            lead = extract_lead(source, leads_dir)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                prepared = prepare_application(
                    lead, PROFILE, POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )

            bundle = apply_posting(prepared.draft_id, data_root=data_root)
            plan = json.loads((prepared.draft_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["surface"], "glassdoor_easy_apply")
            self.assertEqual(bundle["handoff_kind"], "automation_playbook")
            self.assertTrue(bundle["requires_human_submit"])
            self.assertEqual(plan["origin_board"], "glassdoor")
            self.assertEqual(plan["playbook_path"], "playbooks/application/glassdoor-easy-apply.md")

            attempt = record_attempt(prepared.draft_id, {
                "status": "in_progress",
                "checkpoint": "preflight_done",
                "tier_at_attempt": prepared.tier,
                "cover_letter_surface_field_type": "unknown",
                "cover_letter_status": "manual_review_required",
            }, data_root=data_root)
            checkpoint_update(
                prepared.draft_id,
                attempt["attempt_filename"],
                "ready_to_submit",
                data_root=data_root,
            )
            record_attempt(prepared.draft_id, {
                "status": "submitted_provisional",
                "checkpoint": "confirmation_captured",
                "tier_at_attempt": prepared.tier,
                "cover_letter_surface_field_type": "none",
                "cover_letter_status": "skipped_optional_slot_missing",
            }, data_root=data_root)

            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "submitted")
            self.assertTrue(status["requires_human_submit"])

    def test_glassdoor_redirect_flow_reuses_ats_surface_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            leads_dir = data_root / "leads"
            source = data_root / "glassdoor-redirect.json"
            _seed_bank(data_root)
            source.write_text(json.dumps({
                "origin_board": "glassdoor",
                "source": "glassdoor_manual",
                "company": "RedirectCo",
                "title": "Platform Engineer",
                "location": "Remote",
                "application_url": "https://boards.greenhouse.io/redirectco/jobs/42",
                "canonical_url": "https://boards.greenhouse.io/redirectco/jobs/42",
                "posting_url": "https://www.glassdoor.com/job-listing/redirect-role",
                "redirect_chain": [
                    "https://www.glassdoor.com/job-listing/redirect-role",
                    "https://boards.greenhouse.io/redirectco/jobs/42",
                ],
                "raw_description": "Apply on Greenhouse.\nRequirements: Python, AWS.",
            }), encoding="utf-8")

            lead = extract_lead(source, leads_dir)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                prepared = prepare_application(
                    lead, PROFILE, POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )

            bundle = apply_posting(prepared.draft_id, data_root=data_root)
            plan = json.loads((prepared.draft_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["surface"], "greenhouse_redirect")
            self.assertEqual(plan["origin_board"], "glassdoor")
            self.assertEqual(
                plan["correlation_keys"]["origin_posting_url"],
                "https://www.glassdoor.com/job-listing/redirect-role",
            )
            self.assertEqual(
                plan["correlation_keys"]["posting_url"],
                "https://boards.greenhouse.io/redirectco/jobs/42",
            )

    def test_late_glassdoor_handoff_reroutes_without_losing_origin(self) -> None:
        origin_url = "https://www.glassdoor.com/job-listing/late-handoff"
        ats_url = "https://jobs.lever.co/example/123"
        hosted = resolve_application_target(
            {
                "origin_board": "glassdoor",
                "source": "glassdoor_manual",
                "posting_url": origin_url,
                "canonical_url": origin_url,
            },
            origin_url,
        )
        rerouted = resolve_application_target(
            {
                "origin_board": "glassdoor",
                "source": "glassdoor_manual",
                "posting_url": origin_url,
                "application_url": ats_url,
                "canonical_url": ats_url,
                "redirect_chain": [origin_url, ats_url],
            },
            ats_url,
        )
        self.assertEqual(hosted.surface, "glassdoor_easy_apply")
        self.assertEqual(rerouted.surface, "lever_redirect")
        self.assertEqual(rerouted.origin_board, "glassdoor")
        self.assertEqual(rerouted.correlation_keys_patch["origin_posting_url"], origin_url)


if __name__ == "__main__":
    unittest.main()
