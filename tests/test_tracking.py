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

from job_hunt.tracking import (
    TERMINAL_STAGES,
    VALID_STAGES,
    backfill_from_packets,
    check_integrity,
    create_application_status,
    link_generated_content,
    list_applications,
    update_application_status,
)
from job_hunt.utils import read_json, write_json
from job_hunt.schema_checks import validate


class TrackingTest(unittest.TestCase):
    def _schema(self) -> dict:
        return json.loads(
            (ROOT / "schemas" / "application-status.schema.json").read_text(encoding="utf-8")
        )

    def test_create_and_update_application_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            status = create_application_status("lead-abc", d)
            self.assertEqual(status["current_stage"], "not_applied")
            self.assertEqual(status["lead_id"], "lead-abc")
            validate(status, self._schema())

            path = d / "lead-abc-status.json"
            updated = update_application_status(path, "applied", note="Submitted via Greenhouse")
            self.assertEqual(updated["current_stage"], "applied")
            self.assertEqual(len(updated["transitions"]), 1)
            self.assertEqual(updated["transitions"][0]["from_stage"], "not_applied")
            self.assertEqual(updated["transitions"][0]["to_stage"], "applied")
            self.assertEqual(updated["transitions"][0]["note"], "Submitted via Greenhouse")
            validate(updated, self._schema())

            # Advance further.
            updated2 = update_application_status(path, "phone_screen")
            self.assertEqual(updated2["current_stage"], "phone_screen")
            self.assertEqual(len(updated2["transitions"]), 2)
            # Verify all from_stage/to_stage are valid enum values.
            for t in updated2["transitions"]:
                self.assertIn(t["from_stage"], VALID_STAGES)
                self.assertIn(t["to_stage"], VALID_STAGES)
            validate(updated2, self._schema())

    def test_invalid_transition_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-rej", d)
            path = d / "lead-rej-status.json"
            update_application_status(path, "applied")
            update_application_status(path, "rejected")

            with self.assertRaises(ValueError) as ctx:
                update_application_status(path, "applied")
            self.assertIn("terminal", str(ctx.exception).lower())

    def test_noop_transition_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-noop", d)
            path = d / "lead-noop-status.json"
            update_application_status(path, "applied")

            with self.assertRaises(ValueError) as ctx:
                update_application_status(path, "applied")
            self.assertIn("no-op", str(ctx.exception).lower())

    def test_terminal_stage_suppresses_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-term", d)
            path = d / "lead-term-status.json"
            update_application_status(path, "applied")
            result = update_application_status(path, "rejected")
            self.assertTrue(result["follow_up"]["suppress_follow_up"])

    def test_ghosted_allows_reactivation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-ghost", d)
            path = d / "lead-ghost-status.json"
            update_application_status(path, "applied")
            ghosted = update_application_status(path, "ghosted")
            self.assertTrue(ghosted["follow_up"]["suppress_follow_up"])

            # ghosted is semi-terminal — can reactivate.
            reactivated = update_application_status(path, "phone_screen")
            self.assertEqual(reactivated["current_stage"], "phone_screen")

    def test_invalid_stage_name_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-bad", d)
            path = d / "lead-bad-status.json"
            with self.assertRaises(ValueError) as ctx:
                update_application_status(path, "bogus_stage")
            self.assertIn("Invalid stage", str(ctx.exception))

    def test_list_applications_returns_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-1", d)
            update_application_status(d / "lead-1-status.json", "applied")
            create_application_status("lead-2", d)
            update_application_status(d / "lead-2-status.json", "applied")
            update_application_status(d / "lead-2-status.json", "phone_screen")

            result = list_applications(d)
            self.assertEqual(len(result), 2)
            for item in result:
                self.assertIn("lead_id", item)
                self.assertIn("current_stage", item)
                self.assertIn("applied_date", item)

    def test_list_applications_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-a", d)
            update_application_status(d / "lead-a-status.json", "applied")
            create_application_status("lead-b", d)
            update_application_status(d / "lead-b-status.json", "applied")
            update_application_status(d / "lead-b-status.json", "phone_screen")

            applied_only = list_applications(d, stage_filter="applied")
            self.assertEqual(len(applied_only), 1)
            self.assertEqual(applied_only[0]["lead_id"], "lead-a")

    def test_link_generated_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            create_application_status("lead-link", d)
            path = d / "lead-link-status.json"
            link_generated_content(path, "resume-variant-1")
            link_generated_content(path, "cover-letter-1")
            link_generated_content(path, "resume-variant-1")  # duplicate — should not add twice
            status = read_json(path)
            self.assertEqual(status["generated_content_ids"], ["resume-variant-1", "cover-letter-1"])

    def test_check_integrity_detects_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create a content file with no matching status.
            content_dir = root / "generated" / "resumes"
            content_dir.mkdir(parents=True)
            write_json(content_dir / "orphan.json", {
                "content_id": "orphan-content-1",
                "content_type": "resume",
            })
            # Create a status file with no matching lead.
            status_dir = root / "applications"
            status_dir.mkdir(parents=True)
            write_json(status_dir / "missing-lead-status.json", {
                "lead_id": "missing-lead",
                "current_stage": "applied",
                "transitions": [],
                "created_at": "",
                "updated_at": "",
            })

            report = check_integrity(root)
            self.assertIn("orphan-content-1", report["orphaned_content"])
            self.assertIn("missing-lead", report["dangling_leads"])

    def test_check_integrity_detects_dangling_company_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            leads_dir = root / "leads"
            leads_dir.mkdir(parents=True)
            write_json(leads_dir / "lead-x.json", {
                "lead_id": "lead-x",
                "company_research_id": "nonexistent-company",
            })
            companies_dir = root / "companies"
            companies_dir.mkdir(parents=True)
            write_json(companies_dir / "lonely-co.json", {
                "company_id": "lonely-co",
            })

            report = check_integrity(root)
            self.assertIn("nonexistent-company", report["dangling_companies"])
            self.assertIn("lonely-co", report["unreferenced_companies"])

    # Batch 2: check_integrity extensions for new artifact types

    def test_check_integrity_detects_missing_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            resumes = root / "generated" / "resumes"
            resumes.mkdir(parents=True)
            write_json(resumes / "c1.json", {
                "content_id": "c1",
                "output_path": str(root / "ghost.md"),  # does not exist
                "generated_at": "2026-04-16T10:00:00+00:00",
            })
            report = check_integrity(root)
            missing = [e["content_id"] for e in report["missing_source_files"]]
            self.assertIn("c1", missing)

    def test_check_integrity_detects_orphaned_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            resumes = root / "generated" / "resumes"
            resumes.mkdir(parents=True)
            (resumes / "c2.md").write_text("dummy")
            write_json(resumes / "c2.json", {
                "content_id": "c2",
                "output_path": str(resumes / "c2.md"),
                "pdf_path": str(resumes / "c2.pdf"),  # does not exist
                "generated_at": "2026-04-16T10:00:00+00:00",
            })
            report = check_integrity(root)
            orphans = [e["content_id"] for e in report["orphaned_pdfs"]]
            self.assertIn("c2", orphans)

    def test_check_integrity_detects_stale_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            resumes = root / "generated" / "resumes"
            resumes.mkdir(parents=True)
            (resumes / "c3.md").write_text("dummy")
            (resumes / "c3.pdf").write_bytes(b"%PDF")
            write_json(resumes / "c3.json", {
                "content_id": "c3",
                "output_path": str(resumes / "c3.md"),
                "pdf_path": str(resumes / "c3.pdf"),
                "pdf_generated_at": "2026-04-16T10:00:00+00:00",
                "generated_at": "2026-04-16T12:00:00+00:00",  # later than PDF
            })
            report = check_integrity(root)
            stale = [e["content_id"] for e in report["stale_pdfs"]]
            self.assertIn("c3", stale)

    def test_check_integrity_detects_stuck_pending_ats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            resumes = root / "generated" / "resumes"
            resumes.mkdir(parents=True)
            write_json(resumes / "c4.json", {
                "content_id": "c4",
                "generated_at": "2026-04-16T10:00:00+00:00",
                "ats_check": {
                    "status": "pending",
                    "checked_at": "2026-04-16T10:00:01+00:00",
                },
            })
            report = check_integrity(root)
            stuck = [e["content_id"] for e in report["stuck_pending_ats"]]
            self.assertIn("c4", stuck)

    def test_check_integrity_detects_check_failed_ats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            resumes = root / "generated" / "resumes"
            resumes.mkdir(parents=True)
            write_json(resumes / "c5.json", {
                "content_id": "c5",
                "generated_at": "2026-04-16T10:00:00+00:00",
                "ats_check": {
                    "status": "check_failed",
                    "error": "unexpected token in markdown",
                },
            })
            report = check_integrity(root)
            failed = [e["content_id"] for e in report["check_failed_ats"]]
            self.assertIn("c5", failed)

    def test_check_integrity_summary_flags_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Clean run — only an unreferenced company (which doesn't flag as issue)
            companies = root / "companies"
            companies.mkdir(parents=True)
            write_json(companies / "co.json", {"company_id": "co"})
            report = check_integrity(root)
            self.assertFalse(report["summary"]["has_issues"])

    def test_check_integrity_reports_include_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = check_integrity(root)
            self.assertIn("summary", report)
            self.assertIn("issue_counts", report["summary"])
            # All counts should exist and be 0 for an empty data root
            self.assertEqual(report["summary"]["issue_counts"]["orphaned_pdfs"], 0)


def _write_packet(
    data_root: Path,
    draft_id: str,
    lead_id: str,
    *,
    lifecycle_state: str = "drafted",
    disposition_status: str | None = None,
    history: list[dict] | None = None,
) -> None:
    """Write a nested application-packet status.json (the application.py model)."""
    d = data_root / "applications" / draft_id
    d.mkdir(parents=True, exist_ok=True)
    packet: dict = {
        "schema_version": 1,
        "lead_id": lead_id,
        "draft_id": draft_id,
        "current_stage": "not_applied",
        "lifecycle_state": lifecycle_state,
        "transitions": [],
        "attempts": [],
        "events": [],
        "generated_content_ids": [],
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-10T00:00:00+00:00",
    }
    if disposition_status is not None:
        packet["manual_disposition"] = {
            "status": disposition_status,
            "updated_at": "2026-06-10T00:00:00+00:00",
            "history": history or [],
        }
    write_json(d / "status.json", packet)


class BackfillFromPacketsTest(unittest.TestCase):
    def _schema(self) -> dict:
        return json.loads(
            (ROOT / "schemas" / "application-status.schema.json").read_text(encoding="utf-8")
        )

    def test_manually_submitted_becomes_applied_with_historical_ts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_packet(
                root, "acme-apply-1", "acme-lead",
                lifecycle_state="applying", disposition_status="manually_submitted",
                history=[
                    {"at": "2026-06-05T12:00:00+00:00", "status": "manually_submitted"},
                    {"at": "2026-06-07T12:00:00+00:00", "status": "manually_submitted"},
                ],
            )
            roll = backfill_from_packets(root)
            self.assertEqual(roll["applied"], 1)
            rec = read_json(root / "applications" / "acme-lead-status.json")
            self.assertEqual(rec["current_stage"], "applied")
            self.assertTrue(rec["backfilled"])
            # earliest manually_submitted history entry wins
            self.assertEqual(rec["transitions"][0]["timestamp"], "2026-06-05T12:00:00+00:00")
            validate(rec, self._schema())

    def test_withdrawn_lifecycle_becomes_withdrawn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_packet(root, "wd-apply-1", "wd-lead", lifecycle_state="withdrawn")
            roll = backfill_from_packets(root)
            self.assertEqual(roll["withdrawn"], 1)
            rec = read_json(root / "applications" / "wd-lead-status.json")
            self.assertEqual(rec["current_stage"], "withdrawn")
            self.assertTrue(rec["follow_up"]["suppress_follow_up"])

    def test_drafted_and_skipped_are_not_applications(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_packet(root, "d-apply-1", "d-lead", lifecycle_state="drafted")
            _write_packet(root, "s-apply-1", "s-lead", lifecycle_state="drafted",
                          disposition_status="skipped")
            roll = backfill_from_packets(root)
            self.assertEqual(roll["applied"], 0)
            self.assertEqual(roll["withdrawn"], 0)
            self.assertEqual(roll["skipped_not_an_application"], 2)
            self.assertFalse((root / "applications" / "d-lead-status.json").exists())
            self.assertFalse((root / "applications" / "s-lead-status.json").exists())

    def test_idempotent_never_clobbers_existing_flat_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_packet(root, "x-apply-1", "x-lead",
                          disposition_status="manually_submitted",
                          history=[{"at": "2026-06-05T12:00:00+00:00", "status": "manually_submitted"}])
            # Pretend triage already advanced this lead past applied.
            apps = root / "applications"
            apps.mkdir(parents=True, exist_ok=True)
            write_json(apps / "x-lead-status.json", {
                "lead_id": "x-lead", "current_stage": "onsite",
                "transitions": [], "created_at": "2026-06-01T00:00:00+00:00",
                "updated_at": "2026-06-20T00:00:00+00:00",
            })
            roll = backfill_from_packets(root)
            self.assertEqual(roll["skipped_already_present"], 1)
            self.assertEqual(roll["applied"], 0)
            # existing record untouched
            rec = read_json(apps / "x-lead-status.json")
            self.assertEqual(rec["current_stage"], "onsite")

    def test_dry_run_writes_nothing_but_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_packet(root, "dr-apply-1", "dr-lead",
                          disposition_status="manually_submitted",
                          history=[{"at": "2026-06-05T12:00:00+00:00", "status": "manually_submitted"}])
            roll = backfill_from_packets(root, dry_run=True)
            self.assertEqual(roll["applied"], 1)
            self.assertTrue(roll["dry_run"])
            self.assertFalse((root / "applications" / "dr-lead-status.json").exists())

    def test_empty_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            roll = backfill_from_packets(Path(tmpdir))
            self.assertEqual(roll["applied"], 0)
            self.assertEqual(roll["total_packets"], 0)


if __name__ == "__main__":
    unittest.main()
