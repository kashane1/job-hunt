"""Phase 6 MVP — mocked end-to-end integration test.

Walks the full Python pipeline from synthetic Indeed lead through
prepare-application → apply-posting handoff → agent "drives" the playbook
by writing checkpoint attempt files → record-attempt through the
submitted_provisional transition → verify the audit trail.

No real network, no real Chrome MCP. The agent's MCP calls are replaced by
``record_attempt`` + ``checkpoint_update`` calls that reflect the states
each per-surface playbook's Step N would produce. Fixtures for live Indeed
HTML live in the spike todo (#046).

This test is the one called out by the plan's Phase 9 Quality Gate:
    "test_indeed_pipeline.py integration test uses fixture-replay from a
    recorded agent run for at least one end-to-end success path."
In this session we use a hand-stitched happy-path replay; when the spike
captures a real run, swap the replay_attempts list for a fixture load.
"""

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
from job_hunt.utils import write_json


# Synthetic lead shaped like what indeed_discovery → ingest_url → score_lead
# would produce in a real pipeline.
INDEED_LEAD = {
    "lead_id": "indeed-example-senior-platform-abcdef01",
    "company": "ExampleCo",
    "title": "Senior Platform Engineer",
    "location": "Remote",
    "raw_description": (
        "We need a senior platform engineer with Python and AWS experience. "
        "Must be authorized to work in the US."
    ),
    "canonical_url": "https://www.indeed.com/viewjob?jk=0123456789abcdef",
    "normalized_requirements": {
        "keywords": ["python", "aws", "platform"],
        "required": [],
    },
    "fit_assessment": {"matched_skills": ["python", "aws"], "fit_score": 85},
    "status": "scored",
}


PROFILE = {
    "contact": {
        "emails": ["cand@example.com"],
        "phones": ["(555) 555-0100"],
        "links": ["https://www.linkedin.com/in/cand/"],
    },
    "documents": [
        {"document_id": "d1", "document_type": "resume", "title": "R", "source_excerpt": ""},
        {"document_id": "d2", "document_type": "preferences", "title": "P", "source_excerpt": ""},
    ],
    "skills": [
        {"name": "python", "source_document_ids": ["d1"]},
        {"name": "aws", "source_document_ids": ["d1"]},
    ],
    "experience_highlights": [
        {"summary": "Shipped platform work 2021 to 2026", "source_document_ids": ["d1"]},
    ],
    "question_bank": [],
    "preferences": {
        "target_titles": ["Senior SWE", "Senior Platform Engineer"],
        "preferred_locations": ["Remote"],
        "remote_preference": "remote",
        "excluded_keywords": [],
        "work_authorization": "US Citizen",
        "sponsorship_required": False,
        "minimum_compensation": "$140,000",
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


def _seed_bank_for_pipeline(data_root: Path) -> None:
    """Seed every canonical question DEFAULT_FIELD_SET asks about."""
    entries = [
        ("work_auth_yes", "are you legally authorized to work in the united states", "Yes", "yes_no"),
        ("sponsorship_no", "will you now or in the future require sponsorship for employment visa status", "No", "yes_no"),
        ("remote_yes", "are you willing to work remotely", "Yes", "yes_no"),
        ("start_date", "when can you start", "Two weeks", "text"),
        ("min_salary", "what is your minimum salary expectation", "$140,000", "text"),
        ("linkedin", "linkedin url", "https://www.linkedin.com/in/cand/", "text"),
        ("why_role", "why are you interested in this role", "Because the role aligns", "text"),
    ]
    write_json(data_root / "answer-bank.json", {
        "schema_version": 1,
        "entries": [
            {
                "entry_id": eid,
                "canonical_question": q,
                "observed_variants": [],
                "answer": a,
                "answer_format": fmt,
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "reviewed_at": "2026-04-17T00:00:00Z",
                "time_sensitive": False,
                "valid_until": None,
                "created_at": "2026-04-17T00:00:00Z",
                "notes": None,
            }
            for eid, q, a, fmt in entries
        ],
    })


class IndeedPipelineMvpTest(unittest.TestCase):
    def test_full_happy_path_produces_audit_trail(self) -> None:
        """The 6-checkpoint Indeed playbook happy path ends in
        ``submitted_provisional`` with the full audit trail on disk.
        """
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank_for_pipeline(data_root)

            # Step 1: prepare
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                prepared = prepare_application(
                    INDEED_LEAD, PROFILE, POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            self.assertEqual(prepared.tier, "tier_1")
            self.assertEqual(prepared.surface, "indeed_easy_apply")

            # Step 2: apply-posting handoff bundle
            bundle = apply_posting(prepared.draft_id, data_root=data_root)
            self.assertEqual(bundle["surface"], "indeed_easy_apply")
            self.assertEqual(bundle["playbook_path"], "playbooks/application/indeed-easy-apply.md")
            self.assertIn("<untrusted_jd_", bundle["wrapped_jd"])

            # Step 3: agent drives the 6 checkpoints declared by the playbook.
            # The first attempt is an in_progress at preflight_done; we then
            # advance through form_opened / fields_filled / ready_to_submit
            # via lightweight checkpoint_update, and submit via a second
            # record-attempt at submitted_provisional + confirmation_captured.
            preflight = record_attempt(prepared.draft_id, {
                "status": "in_progress",
                "checkpoint": "preflight_done",
                "tier_at_attempt": "tier_1",
            }, data_root=data_root)
            preflight_filename = preflight["attempt_filename"]

            for cp in ("form_opened", "fields_filled", "ready_to_submit"):
                checkpoint_update(
                    prepared.draft_id, preflight_filename, cp,
                    data_root=data_root,
                )

            # Step 4: human clicks Submit; agent resumes and records the
            # submitted_provisional attempt (post-submit capture in playbook).
            record_attempt(prepared.draft_id, {
                "status": "submitted_provisional",
                "checkpoint": "confirmation_captured",
                "tier_at_attempt": "tier_1",
            }, data_root=data_root)

            # Verify audit trail shape.
            attempts_dir = prepared.draft_dir / "attempts"
            attempt_files = sorted(attempts_dir.glob("*.json"))
            self.assertEqual(len(attempt_files), 2)

            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "submitted")
            self.assertEqual(status["tier"], "tier_1")
            # Events are append-only and idempotent; we expect one
            # "submitted" event from the submitted_provisional attempt.
            self.assertEqual([e["type"] for e in status["events"]], ["submitted"])

            # Plan.json retains correlation keys needed by Phase 8 confirmation.
            plan = json.loads((prepared.draft_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["correlation_keys"]["indeed_jk"], "0123456789abcdef")
            self.assertEqual(plan["correlation_keys"]["company"], "ExampleCo")
            self.assertEqual(plan["correlation_keys"]["title"], "Senior Platform Engineer")

    def test_unknown_question_mid_form_triggers_tier_2(self) -> None:
        """When the agent encounters an unknown question mid-flow, the
        attempt is ``paused_tier2`` with ``tier_downgraded_from=tier_1``.
        Lifecycle state stays ``applying`` (per the mapping table).
        """
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank_for_pipeline(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                prepared = prepare_application(
                    INDEED_LEAD, PROFILE, POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            record_attempt(prepared.draft_id, {
                "status": "paused_tier2",
                "checkpoint": "fields_filled",
                "tier_at_attempt": "tier_2",
                "tier_downgraded_from": "tier_1",
            }, data_root=data_root)
            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "applying")

    def test_dry_run_attempt_does_not_advance_lifecycle(self) -> None:
        """A dry_run_only attempt leaves lifecycle_state at ``drafted``."""
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank_for_pipeline(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                prepared = prepare_application(
                    INDEED_LEAD, PROFILE, POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            record_attempt(prepared.draft_id, {
                "status": "dry_run_only",
                "checkpoint": "ready_to_submit",
                "tier_at_attempt": "tier_1",
            }, data_root=data_root)
            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "drafted")


if __name__ == "__main__":
    unittest.main()
