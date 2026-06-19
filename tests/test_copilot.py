from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.copilot import (
    filter_recent_leads,
    lead_effective_timestamp,
    lead_tier,
    parse_since,
    plan_copilot_run,
    render_decision_log_md,
    scan_recent,
    write_copilot_run,
)
from job_hunt.resume_registry import load_registry
from job_hunt.schema_checks import validate
from job_hunt.utils import write_json

SCAN_SCHEMA = json.loads(
    (ROOT / "schemas" / "recent-scan.schema.json").read_text(encoding="utf-8")
)

NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


def _lead(lead_id: str, title: str, ts: str, rec: str = "strong_yes", skills=None) -> dict:
    return {
        "lead_id": lead_id,
        "title": title,
        "company": "Acme",
        "source": "greenhouse",
        "application_url": f"https://x/{lead_id}",
        "ingested_at": ts,
        "fit_assessment": {
            "fit_recommendation": rec,
            "fit_score": 80,
            "matched_skills": skills or ["python"],
            "fit_rationale": "test",
        },
    }


def _write_leads(d: Path, leads: list[dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for lead in leads:
        write_json(d / f"{lead['lead_id']}.json", lead)


class ParseSinceTest(unittest.TestCase):
    def test_durations(self) -> None:
        self.assertEqual(parse_since("1h", now=NOW), datetime(2026, 6, 18, 11, tzinfo=timezone.utc))
        self.assertEqual(parse_since("30m", now=NOW), datetime(2026, 6, 18, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2d", now=NOW), datetime(2026, 6, 16, 12, tzinfo=timezone.utc))
        self.assertEqual(parse_since("1w", now=NOW), datetime(2026, 6, 11, 12, tzinfo=timezone.utc))

    def test_iso(self) -> None:
        self.assertEqual(
            parse_since("2026-06-18T09:00:00Z", now=NOW),
            datetime(2026, 6, 18, 9, tzinfo=timezone.utc),
        )

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_since("yesterday", now=NOW)


class TimestampTest(unittest.TestCase):
    def test_picks_newest(self) -> None:
        lead = {
            "ingested_at": "2026-06-18T08:00:00Z",
            "observed_sources": [
                {"discovered_at": "2026-06-18T10:00:00Z"},
                {"discovered_at": "2026-06-17T10:00:00Z"},
            ],
        }
        self.assertEqual(
            lead_effective_timestamp(lead),
            datetime(2026, 6, 18, 10, tzinfo=timezone.utc),
        )

    def test_none_when_no_timestamps(self) -> None:
        self.assertIsNone(lead_effective_timestamp({"title": "x"}))

    def test_tier(self) -> None:
        self.assertEqual(lead_tier(_lead("a", "t", "2026-06-18T11:00:00Z"))[0], "strong_yes")
        self.assertEqual(lead_tier({"title": "x"}), ("unscored", None))


class FilterRecentTest(unittest.TestCase):
    def test_window_inclusion(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            from job_hunt.copilot import load_leads
            _write_leads(d, [
                _lead("fresh", "Senior AI Engineer", "2026-06-18T11:30:00Z"),
                _lead("stale", "Senior AI Engineer", "2026-06-10T11:30:00Z"),
            ])
            window = parse_since("1h", now=NOW)
            cands = filter_recent_leads(load_leads(d), window)
            ids = {c["lead_id"] for c in cands}
            self.assertEqual(ids, {"fresh"})

    def test_sorted_by_tier(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            from job_hunt.copilot import load_leads
            _write_leads(d, [
                _lead("m", "Engineer", "2026-06-18T11:50:00Z", rec="maybe"),
                _lead("s", "Engineer", "2026-06-18T11:40:00Z", rec="strong_yes"),
            ])
            cands = filter_recent_leads(load_leads(d), parse_since("1h", now=NOW))
            self.assertEqual(cands[0]["lead_id"], "s")  # strong_yes first


class ScanRecentTest(unittest.TestCase):
    def test_counts_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_leads(d, [
                _lead("a", "Senior AI Engineer", "2026-06-18T11:30:00Z", rec="strong_yes"),
                _lead("b", "Backend Engineer", "2026-06-18T11:45:00Z", rec="maybe"),
                _lead("c", "Backend Engineer", "2026-06-01T00:00:00Z", rec="strong_yes"),  # stale
            ])
            scan = scan_recent(d, "1h", now=NOW)
            self.assertEqual(scan["counts"]["total_in_window"], 2)
            self.assertEqual(scan["counts"]["strong_yes"], 1)
            self.assertEqual(scan["counts"]["maybe"], 1)
            validate(scan, SCAN_SCHEMA)


class PlanCopilotRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = load_registry()

    def test_min_tier_gates(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_leads(d, [
                _lead("strong", "Senior AI Engineer", "2026-06-18T11:30:00Z", rec="strong_yes"),
                _lead("weak", "Senior AI Engineer", "2026-06-18T11:30:00Z", rec="maybe"),
            ])
            run = plan_copilot_run(d, "1h", min_tier="strong_yes", registry=self.reg, now=NOW)
            self.assertEqual(run["jobs_planned"], 1)
            self.assertEqual(run["jobs"][0]["lead_id"], "strong")

    def test_human_gate_and_structure(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_leads(d, [_lead("a", "Senior AI Engineer", "2026-06-18T11:30:00Z")])
            run = plan_copilot_run(d, "1h", min_tier="maybe", registry=self.reg, now=NOW)
            self.assertIn("auto_submit_tiers = []", run["human_gate"])
            job = run["jobs"][0]
            self.assertIn("resume_selection", job)
            self.assertIn("why_matched", job)
            self.assertTrue(any("select-resume-variant" in c for c in job["next_commands"]))

    def test_invalid_min_tier_raises(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                plan_copilot_run(Path(t), "1h", min_tier="bogus", registry=self.reg, now=NOW)

    def test_render_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_leads(d, [_lead("a", "Senior AI Engineer", "2026-06-18T11:30:00Z")])
            run = plan_copilot_run(d, "1h", min_tier="maybe", registry=self.reg, now=NOW)
            md = render_decision_log_md(run)
            self.assertIn("Human gate", md)
            self.assertIn("Senior AI Engineer", md)
            run_dir = write_copilot_run(run, Path(t) / "runs")
            self.assertTrue((run_dir / "decision-log.json").exists())
            self.assertTrue((run_dir / "decision-log.md").exists())


if __name__ == "__main__":
    unittest.main()
