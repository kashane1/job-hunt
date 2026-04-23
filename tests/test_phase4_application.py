"""Phase 4 tests for the application pipeline.

Covers prepare_application end-to-end, record_attempt schema + redaction +
locked status merge + checkpoint DAG enforcement, reconcile_stale_attempts
supersedes-chain, apply_posting handoff-bundle shape, tier downgrade mid-
flow, and the mutation helpers (withdraw / reopen / mark_applied_externally /
refresh).

Tests intentionally pass ``data_root`` and ``bank_path`` through every call
so state stays under TemporaryDirectory; none of these tests touch the
repo's real ``data/`` or ``profile/`` dirs.
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
    ApplicationError,
    PlanError,
    _attempt_filename,
    apply_posting,
    apply_status,
    checkpoint_update,
    detect_surface,
    lead_state_from_attempt,
    list_drafts,
    mark_applied_externally,
    playbook_for_surface,
    prepare_application,
    reconcile_stale_attempts,
    record_attempt,
    redact_attempt,
    refresh_application,
    reopen_application,
    withdraw_application,
)
from job_hunt.utils import read_json, write_json


MINIMAL_LEAD = {
    "lead_id": "indeed-abc-senior-engineer-indeed_search",
    "company": "ExampleCo",
    "title": "Senior Software Engineer",
    "location": "Remote",
    "raw_description": "We are looking for someone.\nRequirements: Python, AWS.",
    "canonical_url": "https://www.indeed.com/viewjob?jk=abcdef0123456789",
    "normalized_requirements": {"keywords": ["python", "aws"], "required": []},
    "fit_assessment": {"matched_skills": ["python", "aws"]},
}

LINKEDIN_ASSIST_LEAD = {
    "lead_id": "linkedin-abc-senior-engineer-linkedin_manual",
    "company": "ExampleCo",
    "title": "Senior Software Engineer",
    "location": "Remote",
    "raw_description": "Apply on LinkedIn.\nRequirements: Python, AWS.",
    "canonical_url": "https://www.linkedin.com/jobs/view/1234567890/",
    "application_url": "https://www.linkedin.com/jobs/view/1234567890/",
    "origin_board": "linkedin",
    "source": "linkedin_manual",
    "normalized_requirements": {"keywords": ["python", "aws"], "required": []},
    "fit_assessment": {"matched_skills": ["python", "aws"]},
}

LINKEDIN_REDIRECT_LEAD = {
    **LINKEDIN_ASSIST_LEAD,
    "lead_id": "linkedin-redirect-senior-engineer-linkedin_manual",
    "canonical_url": "https://boards.greenhouse.io/co/jobs/1",
    "application_url": "https://boards.greenhouse.io/co/jobs/1",
    "posting_url": "https://www.linkedin.com/jobs/view/1234567890/",
    "redirect_chain": [
        "https://www.linkedin.com/jobs/view/1234567890/",
        "https://boards.greenhouse.io/co/jobs/1",
    ],
}

MINIMAL_PROFILE = {
    "contact": {
        "emails": ["x@example.com"],
        "phones": ["(555) 555-0100"],
        "links": ["https://www.linkedin.com/in/x/"],
    },
    "documents": [
        {"document_id": "doc-resume", "document_type": "resume", "title": "Resume", "source_excerpt": ""},
        {"document_id": "doc-pref", "document_type": "preferences", "title": "Prefs", "source_excerpt": ""},
    ],
    "skills": [{"name": "python", "source_document_ids": ["doc-resume"]}],
    "experience_highlights": [
        {"summary": "Shipped platform work 2022 to 2026", "source_document_ids": ["doc-resume"]},
    ],
    "question_bank": [],
    "preferences": {
        "target_titles": ["Senior SWE"],
        "preferred_locations": ["Remote"],
        "remote_preference": "remote",
        "excluded_keywords": [],
        "work_authorization": "US Citizen",
        "sponsorship_required": False,
        "minimum_compensation": "$140,000",
    },
}

SIMPLE_POLICY = {
    "approval_required_before_submit": True,
    "approval_required_before_account_creation": True,
    "apply_policy": {"auto_submit_tiers": [], "stale_attempt_threshold_minutes": 0},
}


def _seed_bank(data_root: Path) -> Path:
    """Ship a minimal bank under the temp data_root so resolve() finds hits."""
    path = data_root / "answer-bank.json"
    write_json(path, {
        "schema_version": 1,
        "entries": [
            {
                "entry_id": "work_auth_yes",
                "canonical_question": "are you legally authorized to work in the united states",
                "answer": "Yes",
                "answer_format": "yes_no",
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
            {
                "entry_id": "sponsorship_no",
                "canonical_question": "will you now or in the future require sponsorship for employment visa status",
                "answer": "No",
                "answer_format": "yes_no",
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
            {
                "entry_id": "remote_yes",
                "canonical_question": "are you willing to work remotely",
                "answer": "Yes",
                "answer_format": "yes_no",
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
            {
                "entry_id": "start_date",
                "canonical_question": "when can you start",
                "answer": "Two weeks",
                "answer_format": "text",
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
            {
                "entry_id": "min_salary",
                "canonical_question": "what is your minimum salary expectation",
                "answer": "$140,000",
                "answer_format": "text",
                "source": "curated",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
            {
                "entry_id": "linkedin",
                "canonical_question": "linkedin url",
                "answer": "{{linkedin_url}}",
                "answer_format": "text",
                "source": "curated_template",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
            {
                "entry_id": "why_role",
                "canonical_question": "why are you interested in this role",
                "answer": "{{why_this_role}}",
                "answer_format": "text",
                "source": "curated_template",
                "reviewed": True,
                "deprecated": False,
                "created_at": "2026-04-17T00:00:00Z",
                "observed_variants": [],
            },
        ],
    })
    return path


def _seed_generated_asset(
    data_root: Path,
    *,
    kind: str,
    content_id: str,
    lead_id: str,
    md_name: str,
    pdf: bool = True,
) -> dict:
    mapping = {
        "resume": "resumes",
        "cover_letter": "cover-letters",
    }
    out_dir = data_root / "generated" / mapping[kind]
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / md_name
    md_path.write_text(f"# {content_id}\n", encoding="utf-8")
    record = {
        "content_id": content_id,
        "content_type": kind,
        "variant_style": "test",
        "generated_at": "2026-04-19T00:00:00+00:00",
        "lead_id": lead_id,
        "source_document_ids": [],
        "output_path": str(md_path),
    }
    if pdf:
        pdf_path = md_path.with_suffix(".pdf")
        pdf_path.write_bytes(b"%PDF-1.4\n")
        record["pdf_path"] = str(pdf_path)
        record["pdf_generated_at"] = "2026-04-19T00:00:01+00:00"
    write_json(out_dir / f"{content_id}.json", record)
    return record


class SurfaceDetectionTest(unittest.TestCase):
    def test_indeed_url(self) -> None:
        self.assertEqual(
            detect_surface("https://www.indeed.com/viewjob?jk=abc"),
            "indeed_easy_apply",
        )

    def test_greenhouse_url(self) -> None:
        self.assertEqual(
            detect_surface("https://boards.greenhouse.io/co/jobs/1"),
            "greenhouse_redirect",
        )

    def test_glassdoor_url(self) -> None:
        self.assertEqual(
            detect_surface("https://www.glassdoor.com/job-listing/example-role"),
            "glassdoor_easy_apply",
        )

    def test_unknown_defaults_indeed(self) -> None:
        self.assertEqual(detect_surface("https://example.com/jobs/1"), "indeed_easy_apply")

    def test_every_surface_has_a_playbook(self) -> None:
        for surface in (
            "indeed_easy_apply", "greenhouse_redirect", "lever_redirect",
            "workday_redirect", "ashby_redirect",
            "linkedin_easy_apply", "linkedin_easy_apply_assisted",
            "glassdoor_easy_apply",
        ):
            self.assertTrue(playbook_for_surface(surface))

    def test_linkedin_hosted_routes_to_easy_apply_surface(self) -> None:
        self.assertEqual(
            detect_surface("https://www.linkedin.com/jobs/view/1234567890/"),
            "linkedin_easy_apply",
        )


class RedactionTest(unittest.TestCase):
    def test_key_name_match(self) -> None:
        out = redact_attempt({"password": "hunter2", "session_cookie": "abc"})
        self.assertEqual(out, {"password": "[REDACTED]", "session_cookie": "[REDACTED]"})

    def test_jwt_pattern(self) -> None:
        synthetic = "Here is a token eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOjEyM30.sig in a field"
        out = redact_attempt({"notes": synthetic})
        self.assertIn("[REDACTED]", out["notes"])
        self.assertNotIn("eyJ", out["notes"])

    def test_auth_header(self) -> None:
        out = redact_attempt({"request": "Authorization: Bearer xyz987"})
        self.assertIn("[REDACTED]", out["request"])
        self.assertNotIn("Bearer xyz987", out["request"])

    def test_token_query_param(self) -> None:
        out = redact_attempt({"url": "https://example.com?csrf=deadbeefcafe&other=1"})
        self.assertIn("csrf=[REDACTED]", out["url"])
        self.assertIn("other=1", out["url"])


class PreparePipelineTest(unittest.TestCase):
    def test_prepare_writes_plan_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            # Patch out generation + ats_check to avoid touching generated/.
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            draft_dir = result.draft_dir
            self.assertTrue((draft_dir / "plan.json").exists())
            self.assertTrue((draft_dir / "status.json").exists())
            plan = read_json(draft_dir / "plan.json")
            self.assertEqual(plan["surface"], "indeed_easy_apply")
            self.assertEqual(plan["playbook_path"], "playbooks/application/indeed-easy-apply.md")
            self.assertEqual(plan["correlation_keys"]["indeed_jk"], "abcdef0123456789")
            # Every field has an answer; tier should be tier_1.
            self.assertEqual(plan["tier"], "tier_1")
            self.assertTrue(all(f["provenance"] != "none" for f in plan["fields"]))

    def test_prepare_writes_generated_asset_refs_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])), \
                 patch("job_hunt.application._build_resume_asset_ref", return_value={
                     "content_id": "resume-1",
                     "available": True,
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "ready",
                 }), \
                 patch("job_hunt.application._build_cover_letter_asset_ref", return_value={
                     "content_id": "cover-1",
                     "available": True,
                     "generation_status": "generated",
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "ready",
                 }):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            plan = read_json(result.draft_dir / "plan.json")
            self.assertEqual(plan["generated_asset_refs"]["resume"]["content_id"], "resume-1")
            self.assertEqual(plan["generated_asset_refs"]["cover_letter"]["content_id"], "cover-1")
            self.assertEqual(plan["cover_letter_policy"]["text_area_policy"], "manual_only")
            status = read_json(result.draft_dir / "status.json")
            self.assertEqual(status["generated_content_ids"], ["resume-1", "cover-1"])

    def test_prepare_cover_letter_pdf_failure_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])), \
                 patch("job_hunt.application._build_resume_asset_ref", return_value={
                     "content_id": "resume-1",
                     "available": True,
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "ready",
                 }), \
                 patch("job_hunt.application._build_cover_letter_asset_ref", return_value={
                     "content_id": "cover-1",
                     "available": True,
                     "generation_status": "generated",
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "failed",
                 }):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            plan = read_json(result.draft_dir / "plan.json")
            self.assertEqual(plan["generated_asset_refs"]["cover_letter"]["pdf_export_status"], "failed")
            self.assertEqual(plan["tier"], "tier_1")

    def test_prepare_tier_2_when_field_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            # Ship a bank missing minimum-salary to force tier_2.
            bank = data_root / "answer-bank.json"
            write_json(bank, {"schema_version": 1, "entries": []})
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            plan = read_json(result.draft_dir / "plan.json")
            self.assertEqual(plan["tier"], "tier_2")
            self.assertIn("unresolved_field", plan["tier_rationale"])

    def test_prepare_refuses_existing_draft_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
                with self.assertRaises(PlanError) as ctx:
                    prepare_application(
                        MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                        output_root=data_root / "applications",
                        data_root=data_root,
                    )
                self.assertEqual(ctx.exception.error_code, "draft_already_exists")

    def test_auto_submit_override_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            bad_policy = {
                **SIMPLE_POLICY,
                "apply_policy": {**SIMPLE_POLICY["apply_policy"], "auto_submit_tiers": ["tier_1"]},
            }
            with self.assertRaises(PlanError) as ctx:
                prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, bad_policy,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            self.assertEqual(ctx.exception.error_code, "policy_loosen_attempt")

    def test_prepare_linkedin_hosted_writes_automation_handoff_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    LINKEDIN_ASSIST_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            plan = read_json(result.draft_dir / "plan.json")
            self.assertEqual(plan["origin_board"], "linkedin")
            self.assertEqual(plan["surface"], "linkedin_easy_apply")
            self.assertEqual(plan["handoff_kind"], "automation_playbook")
            self.assertEqual(plan["surface_policy"], "browser_automated_human_submit")
            self.assertTrue(plan["requires_human_submit"])
            self.assertEqual(plan["routing_snapshot"]["surface"], "linkedin_easy_apply")
            status = read_json(result.draft_dir / "status.json")
            self.assertTrue(status["requires_human_submit"])
            self.assertEqual(status["routing_snapshot"]["executor_backend"], "claude_chrome")

    def test_prepare_linkedin_redirect_reuses_ats_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    LINKEDIN_REDIRECT_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            plan = read_json(result.draft_dir / "plan.json")
            self.assertEqual(plan["origin_board"], "linkedin")
            self.assertEqual(plan["surface"], "greenhouse_redirect")
            self.assertEqual(plan["handoff_kind"], "automation_playbook")
            self.assertEqual(
                plan["correlation_keys"]["origin_posting_url"],
                "https://www.linkedin.com/jobs/view/1234567890/",
            )
            self.assertEqual(
                plan["correlation_keys"]["posting_url"],
                "https://boards.greenhouse.io/co/jobs/1",
            )
            self.assertEqual(plan["redirect_chain"][-1], "https://boards.greenhouse.io/co/jobs/1")


class RecordAttemptTest(unittest.TestCase):
    def test_full_record_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            payload = {
                "status": "in_progress",
                "checkpoint": "preflight_done",
            }
            attempt = record_attempt(result.draft_id, payload, data_root=data_root)
            attempts_dir = result.draft_dir / "attempts"
            self.assertTrue(list(attempts_dir.glob("*.json")))
            status = apply_status(result.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "applying")
            self.assertEqual(len(status["attempts"]), 1)

    def test_submitted_provisional_promotes_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            record_attempt(result.draft_id, {
                "status": "in_progress", "checkpoint": "form_opened",
            }, data_root=data_root)
            record_attempt(result.draft_id, {
                "status": "submitted_provisional", "checkpoint": "confirmation_captured",
            }, data_root=data_root)
            status = apply_status(result.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "submitted")
            event_types = [e["type"] for e in status["events"]]
            self.assertIn("submitted", event_types)

    def test_invalid_status_raises_plan_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            with self.assertRaises(PlanError) as ctx:
                record_attempt(result.draft_id, {
                    "status": "INVALID", "checkpoint": "x",
                }, data_root=data_root)
            self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_failed_status_requires_known_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            with self.assertRaises(PlanError) as ctx:
                record_attempt(result.draft_id, {
                    "status": "failed", "checkpoint": "x",
                    "error_code": "not_a_real_code",
                }, data_root=data_root)
            self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_checkpoint_progression_is_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            record_attempt(result.draft_id, {
                "status": "in_progress",
                "checkpoint": "fields_filled",
            }, data_root=data_root)
            with self.assertRaises(ApplicationError) as ctx:
                record_attempt(result.draft_id, {
                    "status": "in_progress",
                    "checkpoint": "form_opened",
                }, data_root=data_root)
            self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")


class ReconcilerTest(unittest.TestCase):
    def test_stale_in_progress_gets_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            # Write an attempt that looks stale.
            from datetime import UTC, datetime, timedelta
            stale_recorded = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            attempt_path = result.draft_dir / "attempts" / _attempt_filename()
            payload = {
                "schema_version": 1,
                "draft_id": result.draft_id,
                "batch_id": "adhoc-old",
                "attempt_filename": attempt_path.name,
                "status": "in_progress",
                "checkpoint": "preflight_done",
                "recorded_at": stale_recorded,
            }
            write_json(attempt_path, payload)
            original_bytes = attempt_path.read_bytes()

            reconciled = reconcile_stale_attempts(
                SIMPLE_POLICY, data_root=data_root,
            )
            self.assertEqual(len(reconciled), 1)
            # Original file byte-identical.
            self.assertEqual(attempt_path.read_bytes(), original_bytes)
            # New file references the original via supersedes.
            new_path = result.draft_dir / "attempts" / reconciled[0]["replacement"]
            new_payload = read_json(new_path)
            self.assertEqual(new_payload["status"], "unknown_outcome")
            self.assertEqual(new_payload["supersedes"], attempt_path.name)

    def test_current_batch_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            from datetime import UTC, datetime, timedelta
            stale_recorded = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            attempt_path = result.draft_dir / "attempts" / _attempt_filename()
            write_json(attempt_path, {
                "schema_version": 1,
                "draft_id": result.draft_id,
                "batch_id": "current-batch-xyz",
                "attempt_filename": attempt_path.name,
                "status": "in_progress",
                "checkpoint": "preflight_done",
                "recorded_at": stale_recorded,
            })
            reconciled = reconcile_stale_attempts(
                SIMPLE_POLICY,
                current_batch_id="current-batch-xyz",
                data_root=data_root,
            )
            self.assertEqual(reconciled, [])


class ApplyPostingTest(unittest.TestCase):
    def test_handoff_bundle_wraps_jd_with_nonce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            bundle = apply_posting(result.draft_id, data_root=data_root)
            self.assertEqual(bundle["status"], "ok")
            self.assertEqual(bundle["tier"], "tier_1")
            # Delimiters present and nonce-tagged.
            self.assertIn("<untrusted_jd_", bundle["wrapped_jd"])
            self.assertIn("</untrusted_jd_", bundle["wrapped_jd"])

    def test_humanize_bundle_shape_for_eligible_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            bundle = apply_posting(result.draft_id, data_root=data_root)
            self.assertIn("humanize", bundle)
            humanize = bundle["humanize"]
            self.assertTrue(humanize["enabled"])
            self.assertIn("jd_read_ms", humanize)
            self.assertIn("page_advance", humanize)
            self.assertIn("per_field", humanize)
            self.assertIn("mcp_call_estimate", humanize)
            for entry in humanize["per_field"]:
                self.assertIn("pre_read_ms", entry)
                self.assertIn("typing", entry)
                self.assertIn("mode", entry["typing"])

    def test_humanize_audit_round_trip_strips_secret_arrays(self) -> None:
        from job_hunt.application import (
            checkpoint_update, latest_humanize_executed, record_attempt,
        )
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            # Agent-style attempt that includes a humanize_executed block
            # WITH chunk_boundaries (which must be stripped on persist).
            attempt = record_attempt(
                result.draft_id,
                {
                    "status": "in_progress",
                    "checkpoint": "preflight_done",
                    "tier_at_attempt": result.tier,
                    "humanize_executed": {
                        "bundle_seed": 123,
                        "typing_mode_used": "word_chunked",
                        "mode_downgraded": False,
                        "per_field": [
                            {
                                "field_index": 0,
                                "pre_read_ms_planned": 2400,
                                "typing": {
                                    "mode": "word_chunked",
                                    "chunk_count": 3,
                                    "chunk_boundaries": [4, 9, 15],
                                    "chunk_delay_ms": [210, 340, 180],
                                },
                            },
                        ],
                    },
                },
                data_root=data_root,
            )
            persisted = read_json(
                result.draft_dir / "attempts" / attempt["attempt_filename"]
            )
            block = persisted["humanize_executed"]
            self.assertEqual(block["typing_mode_used"], "word_chunked")
            entry = block["per_field"][0]
            self.assertNotIn("chunk_boundaries", entry["typing"])
            self.assertNotIn("chunk_delay_ms", entry["typing"])
            self.assertEqual(entry["typing"]["chunk_count"], 3)
            # latest_humanize_executed surfaces the redacted block.
            latest = latest_humanize_executed(result.draft_id, data_root=data_root)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["typing_mode_used"], "word_chunked")

    def test_manual_assist_bundle_resolves_real_asset_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_generated_asset(
                data_root,
                kind="resume",
                content_id="resume-1",
                lead_id=LINKEDIN_ASSIST_LEAD["lead_id"],
                md_name="resume-1.md",
            )
            cover = _seed_generated_asset(
                data_root,
                kind="cover_letter",
                content_id="cover-1",
                lead_id=LINKEDIN_ASSIST_LEAD["lead_id"],
                md_name="cover-1.md",
            )
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])), \
                 patch("job_hunt.application._build_resume_asset_ref", return_value={
                     "content_id": "resume-1",
                     "available": True,
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "ready",
                 }), \
                 patch("job_hunt.application._build_cover_letter_asset_ref", return_value={
                     "content_id": "cover-1",
                     "available": True,
                     "generation_status": "generated",
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "ready",
                 }):
                result = prepare_application(
                    LINKEDIN_ASSIST_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            bundle = apply_posting(result.draft_id, data_root=data_root)
            self.assertEqual(bundle["handoff_kind"], "automation_playbook")
            self.assertTrue(bundle["requires_human_submit"])
            self.assertEqual(bundle["resume_upload_kind"], "pdf")
            self.assertTrue(bundle["resume_path"].endswith(".pdf"))
            self.assertEqual(bundle["cover_letter_pdf_path"], cover["pdf_path"])
            self.assertEqual(bundle["cover_letter_md_path"], cover["output_path"])
            self.assertTrue(bundle["cover_letter_available"])
            self.assertIn("cover-letter PDF", bundle["cover_letter_review_note"])

    def test_bundle_handles_unavailable_cover_letter_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])), \
                 patch("job_hunt.application._build_resume_asset_ref", return_value={
                     "content_id": None,
                     "available": False,
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "unavailable",
                 }), \
                 patch("job_hunt.application._build_cover_letter_asset_ref", return_value={
                     "content_id": None,
                     "available": False,
                     "generation_status": "failed",
                     "preferred_upload_kind": "pdf",
                     "pdf_export_status": "not_attempted",
                 }):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            bundle = apply_posting(result.draft_id, data_root=data_root)
            self.assertIsNone(bundle["cover_letter_path"])
            self.assertFalse(bundle["cover_letter_available"])
            self.assertIn("No prepared cover-letter asset", bundle["cover_letter_review_note"])


class MutationsTest(unittest.TestCase):
    def _prepared(self, tmp: Path):
        _seed_bank(tmp)
        with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
            return prepare_application(
                MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                output_root=tmp / "applications", data_root=tmp,
            )

    def test_withdraw_sets_state_and_logs_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            result = self._prepared(data_root)
            withdraw_application(result.draft_id, "changed mind", data_root=data_root)
            status = apply_status(result.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "withdrawn")
            self.assertTrue(any(e["type"] == "withdrawn" for e in status["events"]))

    def test_reopen_from_unknown_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            result = self._prepared(data_root)
            status_path = result.draft_dir / "status.json"
            s = read_json(status_path)
            s["lifecycle_state"] = "unknown_outcome"
            write_json(status_path, s)
            reopen_application(result.draft_id, data_root=data_root)
            refreshed = apply_status(result.draft_id, data_root=data_root)
            self.assertEqual(refreshed["lifecycle_state"], "drafted")

    def test_mark_applied_externally_creates_fresh_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            mark_applied_externally("never-prepared-lead", note="applied on mobile", data_root=data_root)
            drafts = list_drafts(data_root=data_root)
            matched = [d for d in drafts if d["lead_id"] == "never-prepared-lead"]
            self.assertEqual(len(matched), 1)

    def test_list_drafts_source_matches_origin_board_and_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                linkedin_manual = prepare_application(
                    LINKEDIN_ASSIST_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
                linkedin_redirect = prepare_application(
                    LINKEDIN_REDIRECT_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            linkedin_drafts = list_drafts(source="linkedin", data_root=data_root)
            self.assertEqual(
                {draft["draft_id"] for draft in linkedin_drafts},
                {linkedin_manual.draft_id, linkedin_redirect.draft_id},
            )
            greenhouse_drafts = list_drafts(source="greenhouse_redirect", data_root=data_root)
            self.assertEqual(
                [draft["draft_id"] for draft in greenhouse_drafts],
                [linkedin_redirect.draft_id],
            )
            self.assertEqual(linkedin_drafts[0]["origin_board"], "linkedin")

    def test_refresh_application_bumps_snapshot_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            result = self._prepared(data_root)
            plan = refresh_application(result.draft_id, MINIMAL_PROFILE, data_root=data_root)
            self.assertGreater(plan["profile_snapshot"]["snapshot_version"], 1)


class LeadStateMappingTest(unittest.TestCase):
    def test_exhaustive_mapping(self) -> None:
        cases = {
            "in_progress": "applying",
            "paused_tier2": "applying",
            "paused_unknown_question": "applying",
            "submitted_provisional": "submitted",
            "submitted_confirmed": "confirmed",
            "dry_run_only": "drafted",
            "failed": "failed",
            "unknown_outcome": "unknown_outcome",
            "paused_manual_assist": "awaiting_human_action",
            "paused_human_abort": "awaiting_human_action",
        }
        for status, expected in cases.items():
            self.assertEqual(
                lead_state_from_attempt({"status": status}),
                expected,
                f"status={status}",
            )


class CheckpointUpdateTest(unittest.TestCase):
    def test_updates_latest_attempt_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = prepare_application(
                    MINIMAL_LEAD, MINIMAL_PROFILE, SIMPLE_POLICY,
                    output_root=data_root / "applications",
                    data_root=data_root,
                )
            rec = record_attempt(result.draft_id, {
                "status": "in_progress", "checkpoint": "preflight_done",
            }, data_root=data_root)
            filename = rec["attempt_filename"]
            checkpoint_update(result.draft_id, filename, "form_opened", data_root=data_root)
            status = apply_status(result.draft_id, data_root=data_root)
            self.assertEqual(status["attempts"][-1]["checkpoint"], "form_opened")


class RecomputeTiersBackfillTest(unittest.TestCase):
    """Phase 6 — one-shot migration for records demoted solely by the old
    warnings → tier_2 rule. Idempotent and surgical."""

    def _write(self, dir_: Path, name: str, record: dict) -> Path:
        from job_hunt.utils import write_json
        path = dir_ / name
        write_json(path, record)
        return path

    def test_migrates_warnings_only_demotion(self) -> None:
        from job_hunt.application import recompute_tiers
        from job_hunt.utils import read_json
        with tempfile.TemporaryDirectory() as tmpdir:
            apps = Path(tmpdir)
            path = self._write(apps, "only-warnings-status.json", {
                "draft_id": "a1",
                "tier": "tier_2",
                "tier_rationale": "ats_status:warnings",
            })
            result = recompute_tiers(apps)
            self.assertEqual(result["updated"], 1)
            record = read_json(path)
            self.assertEqual(record["tier"], "tier_1")
            self.assertEqual(record["tier_rationale"], "")
            self.assertIn("tier_recomputed_at", record)

    def test_leaves_unresolved_field_demotions_alone(self) -> None:
        from job_hunt.application import recompute_tiers
        from job_hunt.utils import read_json
        with tempfile.TemporaryDirectory() as tmpdir:
            apps = Path(tmpdir)
            path = self._write(apps, "unresolved-status.json", {
                "draft_id": "a2",
                "tier": "tier_2",
                "tier_rationale": "unresolved_field:expected_compensation",
            })
            result = recompute_tiers(apps)
            self.assertEqual(result["updated"], 0)
            record = read_json(path)
            self.assertEqual(record["tier"], "tier_2")

    def test_leaves_errors_and_tier1_alone(self) -> None:
        from job_hunt.application import recompute_tiers
        with tempfile.TemporaryDirectory() as tmpdir:
            apps = Path(tmpdir)
            self._write(apps, "e-status.json", {
                "draft_id": "a3", "tier": "tier_2",
                "tier_rationale": "ats_status:errors",
            })
            self._write(apps, "ok-status.json", {
                "draft_id": "a4", "tier": "tier_1", "tier_rationale": "",
            })
            result = recompute_tiers(apps)
            self.assertEqual(result["updated"], 0)

    def test_idempotent_on_second_run(self) -> None:
        from job_hunt.application import recompute_tiers
        with tempfile.TemporaryDirectory() as tmpdir:
            apps = Path(tmpdir)
            self._write(apps, "m-status.json", {
                "draft_id": "a5", "tier": "tier_2",
                "tier_rationale": "ats_status:warnings",
            })
            first = recompute_tiers(apps)
            second = recompute_tiers(apps)
            self.assertEqual(first["updated"], 1)
            self.assertEqual(second["updated"], 0)

    def test_skips_symlinked_status_files(self) -> None:
        # Symlinks pointing outside applications_dir must not be written
        # back through — would enable targeted overwrite.
        from job_hunt.application import recompute_tiers
        from job_hunt.utils import read_json
        with tempfile.TemporaryDirectory() as tmpdir_a, \
             tempfile.TemporaryDirectory() as tmpdir_b:
            apps = Path(tmpdir_a)
            outside = Path(tmpdir_b) / "outside-status.json"
            outside_record = {
                "draft_id": "victim",
                "tier": "tier_2",
                "tier_rationale": "ats_status:warnings",
            }
            from job_hunt.utils import write_json
            write_json(outside, outside_record)
            # Create a symlink inside apps/ pointing at the outside file.
            link = apps / "malicious-status.json"
            link.symlink_to(outside)
            result = recompute_tiers(apps)
            self.assertEqual(result["updated"], 0)
            self.assertEqual(result["skipped"], 1)
            # Outside file must be untouched (same tier, no tier_recomputed_at).
            survivor = read_json(outside)
            self.assertEqual(survivor["tier"], "tier_2")
            self.assertNotIn("tier_recomputed_at", survivor)


if __name__ == "__main__":
    unittest.main()
