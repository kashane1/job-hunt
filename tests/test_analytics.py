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

from job_hunt.analytics import (
    CALLBACK_STAGES,
    MIN_SAMPLE_FOR_CONFIDENCE,
    MIN_SAMPLE_FOR_RATES,
    MIN_SCORED_LEADS_FOR_GAP,
    MIN_TERMINAL_FOR_REJECTION,
    TERMINAL_STAGES,
    build_aggregator,
    report_dashboard,
    report_rejection_patterns,
    report_skills_gap,
)
from job_hunt.utils import write_json


STAGE_ORDER = ["not_applied", "applied", "phone_screen", "technical", "onsite", "offer", "accepted"]


def _write_status(
    data_root: Path,
    lead_id: str,
    current_stage: str,
    applied_date: str,
    generated_content_ids: list[str] | None = None,
    skip_to_stage: bool = False,
) -> None:
    """Write an application-status record.

    By default, emits a realistic chain of transitions from applied through
    current_stage so that reached-stage checks work naturally. Set
    skip_to_stage=True to emit a single jump from applied directly to
    current_stage (mimicking a lead that got a fast-track interview).
    """
    d = data_root / "applications"
    d.mkdir(parents=True, exist_ok=True)
    transitions = [{"from_stage": "not_applied", "to_stage": "applied", "timestamp": applied_date}]
    if current_stage != "applied" and current_stage != "not_applied":
        if skip_to_stage or current_stage in ("rejected", "withdrawn", "ghosted"):
            transitions.append({
                "from_stage": "applied",
                "to_stage": current_stage,
                "timestamp": applied_date,
            })
        else:
            # Walk through STAGE_ORDER emitting each step
            try:
                start = STAGE_ORDER.index("applied")
                end = STAGE_ORDER.index(current_stage)
            except ValueError:
                # Unknown stage — just direct jump
                transitions.append({
                    "from_stage": "applied",
                    "to_stage": current_stage,
                    "timestamp": applied_date,
                })
            else:
                for i in range(start, end):
                    transitions.append({
                        "from_stage": STAGE_ORDER[i],
                        "to_stage": STAGE_ORDER[i + 1],
                        "timestamp": applied_date,
                    })
    write_json(d / f"{lead_id}-status.json", {
        "lead_id": lead_id,
        "current_stage": current_stage,
        "transitions": transitions,
        "generated_content_ids": generated_content_ids or [],
        "created_at": applied_date,
        "updated_at": applied_date,
    })


def _write_lead(data_root: Path, lead_id: str, **extra: object) -> None:
    d = data_root / "leads"
    d.mkdir(parents=True, exist_ok=True)
    lead = {
        "lead_id": lead_id,
        "fingerprint": lead_id.split("-")[-1],
        "source": "test",
        "application_url": "https://example.com/j/1",
        "company": "ExampleCorp",
        "title": "Senior Engineer",
        "location": "Remote",
        "raw_description": "...",
        "normalized_requirements": {"required": [], "preferred": [], "keywords": []},
        "fit_assessment": {},
        "status": "shortlisted",
    }
    lead.update(extra)
    write_json(d / f"{lead_id}.json", lead)


def _write_company(data_root: Path, company_id: str, **extra: object) -> None:
    d = data_root / "companies"
    d.mkdir(parents=True, exist_ok=True)
    company = {
        "company_id": company_id,
        "company_name": company_id.replace("-", " ").title(),
        "researched_at": "2026-04-16T10:00:00+00:00",
    }
    company.update(extra)
    write_json(d / f"{company_id}.json", company)


def _write_content(data_root: Path, content_id: str, lead_id: str, variant_style: str) -> None:
    d = data_root / "generated" / "resumes"
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / f"{content_id}.json", {
        "content_id": content_id,
        "content_type": "resume",
        "variant_style": variant_style,
        "generated_at": "2026-04-16T10:00:00+00:00",
        "lead_id": lead_id,
        "source_document_ids": [],
    })


class BuildAggregatorTest(unittest.TestCase):
    def test_empty_data_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rows, missing = build_aggregator(Path(tmpdir))
            self.assertEqual(rows, [])
            self.assertEqual(missing["missing_lead_refs"], 0)

    def test_joins_lead_and_company(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_company(root, "exampleco", industry="Tech", stage="growth", remote_policy="remote")
            _write_lead(root, "l1", company_research_id="exampleco")
            _write_status(root, "l1", "applied", "2026-04-10T10:00:00+00:00")
            rows, missing = build_aggregator(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["lead_company"], "ExampleCorp")
            self.assertEqual(rows[0]["company_industry"], "Tech")
            self.assertEqual(rows[0]["company_remote_policy"], "remote")
            self.assertEqual(missing["missing_company_refs"], 0)

    def test_missing_lead_ref_is_counted_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_status(root, "ghost-lead", "applied", "2026-04-10T10:00:00+00:00")
            rows, missing = build_aggregator(root)
            self.assertEqual(len(rows), 1)  # still included
            self.assertEqual(rows[0]["lead_company"], "")
            self.assertEqual(missing["missing_lead_refs"], 1)

    def test_missing_company_ref_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_lead(root, "l1", company_research_id="missing-company")
            _write_status(root, "l1", "applied", "2026-04-10T10:00:00+00:00")
            rows, missing = build_aggregator(root)
            self.assertEqual(missing["missing_company_refs"], 1)

    def test_applied_date_extracted_from_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_lead(root, "l1")
            _write_status(root, "l1", "phone_screen", "2026-04-10T10:00:00+00:00")
            rows, _ = build_aggregator(root)
            self.assertEqual(rows[0]["applied_date"], "2026-04-10T10:00:00+00:00")


class DashboardInsufficientDataTest(unittest.TestCase):
    def test_empty_reports_insufficient_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = report_dashboard(Path(tmpdir))
            self.assertEqual(result["confidence"], "insufficient_data")
            self.assertEqual(result["sample_size"], 0)
            self.assertIn("guidance", result)

    def test_below_threshold_still_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(MIN_SAMPLE_FOR_RATES - 1):
                _write_lead(root, f"l{i}")
                _write_status(root, f"l{i}", "applied", f"2026-04-10T10:00:0{i}+00:00")
            result = report_dashboard(root)
            self.assertEqual(result["confidence"], "insufficient_data")


class DashboardRateReportingTest(unittest.TestCase):
    def _build_test_data(self, root: Path, count: int, callback_count: int) -> None:
        for i in range(count):
            _write_lead(root, f"l{i}")
            stage = "phone_screen" if i < callback_count else "rejected"
            _write_status(root, f"l{i}", stage, f"2026-04-10T10:00:{i:02d}+00:00")

    def test_ten_apps_gives_low_confidence_with_rates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._build_test_data(root, 12, 3)
            result = report_dashboard(root)
            self.assertEqual(result["confidence"], "low")
            self.assertEqual(result["sample_size"], 12)
            self.assertIn("callback_rate", result)
            self.assertAlmostEqual(result["callback_rate"], 3 / 12, places=3)

    def test_thirty_apps_gives_ok_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._build_test_data(root, 35, 10)
            result = report_dashboard(root)
            self.assertEqual(result["confidence"], "ok")
            self.assertEqual(result["sample_size"], 35)

    def test_variant_rates_computed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # 10 apps, 5 with variant A that callback, 5 with variant B that reject
            for i in range(10):
                _write_lead(root, f"l{i}")
                if i < 5:
                    _write_content(root, f"c{i}", f"l{i}", "impact_focused")
                    _write_status(root, f"l{i}", "phone_screen", f"2026-04-10T10:00:{i:02d}+00:00", generated_content_ids=[f"c{i}"])
                else:
                    _write_content(root, f"c{i}", f"l{i}", "technical_depth")
                    _write_status(root, f"l{i}", "rejected", f"2026-04-10T10:00:{i:02d}+00:00", generated_content_ids=[f"c{i}"])
            result = report_dashboard(root)
            self.assertIn("variant_rates", result)
            self.assertAlmostEqual(result["variant_rates"]["impact_focused"]["callback_rate"], 1.0, places=3)
            self.assertAlmostEqual(result["variant_rates"]["technical_depth"]["callback_rate"], 0.0, places=3)

    def test_stage_conversions_computed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Build 10 leads: 10 apply, 5 reach phone_screen, 2 reach technical
            for i in range(10):
                _write_lead(root, f"l{i}")
                if i < 2:
                    stage = "technical"
                elif i < 5:
                    stage = "phone_screen"
                else:
                    stage = "applied"
                _write_status(root, f"l{i}", stage, f"2026-04-10T10:00:{i:02d}+00:00")
            result = report_dashboard(root)
            conversions = result["stage_conversions"]
            self.assertEqual(conversions["applied_to_phone_screen"]["from"], 10)
            self.assertEqual(conversions["applied_to_phone_screen"]["to"], 5)
            self.assertAlmostEqual(conversions["applied_to_phone_screen"]["rate"], 0.5, places=3)


class SkillsGapTest(unittest.TestCase):
    def test_insufficient_data_when_few_scored_leads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = report_skills_gap(Path(tmpdir), profile={"skills": []})
            self.assertEqual(result["confidence"], "insufficient_data")

    def test_ranks_missing_skills_by_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # 12 scored leads, all missing "rust" but only 3 missing "go"
            for i in range(12):
                missing = ["rust"]
                if i < 3:
                    missing.append("go")
                _write_lead(
                    root, f"l{i}",
                    fit_assessment={"fit_score": 70 + i, "matched_skills": [], "missing_skills": missing},
                )
                _write_status(root, f"l{i}", "applied", f"2026-04-10T10:00:{i:02d}+00:00")
            profile = {"skills": [{"name": "python"}, {"name": "postgres"}]}
            result = report_skills_gap(root, profile)
            self.assertEqual(result["confidence"], "low")
            self.assertGreater(len(result["gaps"]), 0)
            skills = [g["skill"] for g in result["gaps"]]
            # Rust should rank higher (12 occurrences vs 3)
            self.assertLess(skills.index("rust"), skills.index("go"))

    def test_canonicalizes_postgres_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Lead requires "postgresql" (an alias); profile has "postgres" (canonical).
            for i in range(12):
                _write_lead(
                    root, f"l{i}",
                    fit_assessment={"fit_score": 80, "missing_skills": ["postgresql"]},
                )
                _write_status(root, f"l{i}", "applied", f"2026-04-10T10:00:{i:02d}+00:00")
            profile = {"skills": [{"name": "postgres"}]}
            # Use fallback taxonomy (batch 1 SKILL_ALIASES)
            result = report_skills_gap(root, profile, taxonomy_path=None)
            skills = [g["skill"] for g in result["gaps"]]
            # postgresql should be canonicalized to postgres and then excluded
            self.assertNotIn("postgresql", skills)
            self.assertNotIn("postgres", skills)


class RejectionPatternsTest(unittest.TestCase):
    def test_insufficient_when_few_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = report_rejection_patterns(Path(tmpdir))
            self.assertEqual(result["confidence"], "insufficient_data")

    def test_separates_ghosted_from_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(6):
                _write_lead(root, f"r{i}")
                _write_status(root, f"r{i}", "rejected", f"2026-04-10T10:00:0{i}+00:00")
            for i in range(5):
                _write_lead(root, f"g{i}")
                _write_status(root, f"g{i}", "ghosted", f"2026-04-10T10:00:0{i}+00:00")
            result = report_rejection_patterns(root)
            self.assertEqual(result["confidence"], "low")
            self.assertEqual(result["breakdown"]["rejected"], 6)
            self.assertEqual(result["breakdown"]["ghosted"], 5)

    def test_surfaces_ghost_rate_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for i in range(4):
                _write_lead(root, f"r{i}")
                _write_status(root, f"r{i}", "rejected", f"2026-04-10T10:00:0{i}+00:00")
            for i in range(8):
                _write_lead(root, f"g{i}")
                _write_status(root, f"g{i}", "ghosted", f"2026-04-10T10:00:0{i}+00:00")
            result = report_rejection_patterns(root)
            obs_text = " ".join(result["observations"]).lower()
            self.assertIn("ghost", obs_text)


if __name__ == "__main__":
    unittest.main()
