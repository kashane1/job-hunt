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
    lead_posted_timestamp,
    lead_seen_timestamp,
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


class TopCandidatesTest(unittest.TestCase):
    @staticmethod
    def _c(lead_id, score, ts="2026-06-18T11:30:00+00:00", tier="strong_yes"):
        return {"lead_id": lead_id, "fit_score": score, "effective_timestamp": ts,
                "tier": tier, "application_url": f"https://x/{lead_id}"}

    def test_ranks_by_fit_score_desc(self) -> None:
        from job_hunt.copilot import top_candidates
        cands = [self._c("low", 60), self._c("high", 95), self._c("mid", 80)]
        ranked = top_candidates(cands, 3)
        self.assertEqual([c["lead_id"] for c in ranked], ["high", "mid", "low"])

    def test_limit_and_unscored_sorts_last(self) -> None:
        from job_hunt.copilot import top_candidates
        cands = [self._c("a", 70), self._c("u", None, tier="unscored"), self._c("b", 90)]
        ranked = top_candidates(cands, 2)
        self.assertEqual([c["lead_id"] for c in ranked], ["b", "a"])  # unscored excluded by limit
        self.assertEqual(top_candidates(cands, 5)[-1]["lead_id"], "u")  # unscored last

    def test_non_positive_n_returns_empty(self) -> None:
        from job_hunt.copilot import top_candidates
        self.assertEqual(top_candidates([self._c("a", 70)], 0), [])


def _lead_pd(lead_id: str, posted: str, ingested: str, rec: str = "strong_yes") -> dict:
    """Lead with a distinct board posting date (observed_sources) and ingest date."""
    return {
        "lead_id": lead_id,
        "title": "Backend Engineer",
        "company": "Acme",
        "source": "greenhouse",
        "application_url": f"https://x/{lead_id}",
        "ingested_at": ingested,
        "observed_sources": [
            {"source": "greenhouse", "discovered_at": ingested, "listing_updated_at": posted},
        ],
        "fit_assessment": {"fit_recommendation": rec, "fit_score": 80, "fit_rationale": "t"},
    }


class PostedVsSeenWindowTest(unittest.TestCase):
    # Posted 10 days before NOW, but ingested 1 minute before NOW.
    OLD_POSTED = "2026-06-08T12:00:00+00:00"
    JUST_INGESTED = "2026-06-18T11:59:00+00:00"

    def test_posted_timestamp_reads_listing_date(self) -> None:
        lead = _lead_pd("a", self.OLD_POSTED, self.JUST_INGESTED)
        self.assertEqual(
            lead_posted_timestamp(lead),
            datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(
            lead_seen_timestamp(lead),
            datetime(2026, 6, 18, 11, 59, tzinfo=timezone.utc))

    def test_posted_mode_excludes_old_posting_ingested_now(self) -> None:
        # The exact bug: a stale posting freshly ingested floods the seen window
        # but must NOT appear in the honest posted window.
        leads = [(Path("a.json"), _lead_pd("a", self.OLD_POSTED, self.JUST_INGESTED))]
        window = parse_since("1h", now=NOW)
        self.assertEqual(filter_recent_leads(leads, window, by="posted"), [])
        seen = filter_recent_leads(leads, window, by="seen")
        self.assertEqual([c["lead_id"] for c in seen], ["a"])
        self.assertEqual(seen[0]["timestamp_basis"], "seen")

    def test_posted_mode_includes_fresh_posting(self) -> None:
        fresh = "2026-06-18T11:50:00+00:00"
        leads = [(Path("a.json"), _lead_pd("a", fresh, self.JUST_INGESTED))]
        cands = filter_recent_leads(leads, parse_since("1h", now=NOW), by="posted")
        self.assertEqual([c["lead_id"] for c in cands], ["a"])
        self.assertEqual(cands[0]["timestamp_basis"], "posted")

    def test_no_posting_date_falls_back_to_seen(self) -> None:
        # Only ingested_at, no listing date: posted-mode must still include it,
        # flagged as seen_fallback (never silently dropped).
        lead = {"lead_id": "n", "title": "T", "company": "Acme",
                "ingested_at": self.JUST_INGESTED,
                "fit_assessment": {"fit_recommendation": "maybe", "fit_score": 50}}
        self.assertIsNone(lead_posted_timestamp(lead))
        cands = filter_recent_leads(
            [(Path("n.json"), lead)], parse_since("1h", now=NOW), by="posted")
        self.assertEqual([c["lead_id"] for c in cands], ["n"])
        self.assertEqual(cands[0]["timestamp_basis"], "seen_fallback")

    def test_scan_recent_by_posted_counts_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _write_leads(d, [
                _lead_pd("fresh", "2026-06-18T11:50:00+00:00", self.JUST_INGESTED),
                _lead_pd("stale", self.OLD_POSTED, self.JUST_INGESTED),
            ])
            # Lead with no posting date (fallback).
            write_json(d / "nopd.json", {
                "lead_id": "nopd", "title": "T", "company": "Acme",
                "ingested_at": self.JUST_INGESTED,
                "fit_assessment": {"fit_recommendation": "maybe", "fit_score": 50}})
            posted = scan_recent(d, "1h", now=NOW, by="posted")
            ids = {c["lead_id"] for c in posted["candidates"]}
            self.assertEqual(ids, {"fresh", "nopd"})  # stale excluded
            self.assertEqual(posted["by"], "posted")
            self.assertEqual(posted["counts"]["posting_date_unknown"], 1)
            validate(posted, SCAN_SCHEMA)
            seen = scan_recent(d, "1h", now=NOW, by="seen")
            self.assertEqual(seen["counts"]["total_in_window"], 3)  # all freshly ingested
            self.assertEqual(seen["counts"]["posting_date_unknown"], 0)

    def test_invalid_by_raises(self) -> None:
        with self.assertRaises(ValueError):
            scan_recent(Path("."), "1h", now=NOW, by="bogus")


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
