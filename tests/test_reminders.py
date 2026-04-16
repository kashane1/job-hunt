from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.reminders import check_follow_ups, generate_follow_up_draft, suppress_follow_up
from job_hunt.tracking import create_application_status, update_application_status
from job_hunt.utils import read_json, write_json


class RemindersTest(unittest.TestCase):
    def _create_applied_status(self, tmpdir: Path, lead_id: str, days_ago: int) -> Path:
        """Create a status that was applied `days_ago` days in the past."""
        create_application_status(lead_id, tmpdir)
        path = tmpdir / f"{lead_id}-status.json"
        status = read_json(path)

        # Manually set applied transition in the past.
        applied_ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
        status["current_stage"] = "applied"
        status["transitions"] = [{
            "from_stage": "not_applied",
            "to_stage": "applied",
            "timestamp": applied_ts,
            "note": "",
        }]
        status["updated_at"] = applied_ts
        write_json(path, status)
        return path

    def test_check_follow_ups_identifies_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            # Applied 12 days ago — should trigger check_in (10 days).
            self._create_applied_status(d, "lead-old", 12)
            # Applied 2 days ago — too recent.
            self._create_applied_status(d, "lead-new", 2)

            result = check_follow_ups(d)
            lead_ids = [r["lead_id"] for r in result]
            self.assertIn("lead-old", lead_ids)
            self.assertNotIn("lead-new", lead_ids)
            # Check the follow-up type.
            for r in result:
                if r["lead_id"] == "lead-old":
                    self.assertEqual(r["follow_up_type"], "check_in")

    def test_follow_up_suppressed_on_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            path = self._create_applied_status(d, "lead-rej", 15)
            update_application_status(path, "rejected")

            result = check_follow_ups(d)
            for r in result:
                if r["lead_id"] == "lead-rej":
                    self.assertTrue(r["suppressed"])

    def test_generate_follow_up_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            result = generate_follow_up_draft(
                lead_id="lead-test",
                candidate_name="Test Person",
                company_name="ExampleCo",
                job_title="Staff Engineer",
                matched_skills=["Python", "AWS"],
                follow_up_type="check_in",
                output_dir=d,
            )
            self.assertTrue(Path(result["path"]).exists())
            content = Path(result["path"]).read_text(encoding="utf-8")
            self.assertIn("ExampleCo", content)
            self.assertIn("Staff Engineer", content)
            self.assertIn("Python", content)

    def test_generate_follow_up_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            for ftype in ("check_in", "follow_up"):
                result = generate_follow_up_draft(
                    lead_id="lead-type", candidate_name="Person",
                    company_name="Co", job_title="Role",
                    matched_skills=[], follow_up_type=ftype,
                    output_dir=d / ftype,
                )
                self.assertTrue(Path(result["path"]).exists())

    def test_suppress_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            self._create_applied_status(d, "lead-sup", 15)
            path = d / "lead-sup-status.json"
            result = suppress_follow_up(path, "Company said no")
            self.assertTrue(result["follow_up"]["suppress_follow_up"])
            self.assertIn("Company said no", result["outcome_notes"])


if __name__ == "__main__":
    unittest.main()
