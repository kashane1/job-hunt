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

from job_hunt.resume_registry import (
    RegistryError,
    infer_seniority,
    load_registry,
    pick_registry_resume,
    route_lead,
)
from job_hunt.schema_checks import validate

SELECTION_SCHEMA = json.loads(
    (ROOT / "schemas" / "resume-selection.schema.json").read_text(encoding="utf-8")
)


def _scored_lead(title: str, skills: list[str], rec: str = "strong_yes", score: int = 80) -> dict:
    return {
        "lead_id": "test-lead",
        "title": title,
        "company": "Acme",
        "fit_assessment": {
            "fit_recommendation": rec,
            "fit_score": score,
            "matched_skills": skills,
            "fit_rationale": "test",
        },
    }


class LoadRegistryTest(unittest.TestCase):
    def test_loads_repo_registry(self) -> None:
        reg = load_registry()
        self.assertEqual(reg["schema_version"], 1)
        ids = {v["id"] for v in reg["variants"]}
        self.assertIn(reg["default_variant"], ids)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(RegistryError):
            load_registry(Path("/nonexistent/registry.json"))

    def test_duplicate_ids_raise(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "r.json"
            p.write_text(json.dumps({
                "schema_version": 1,
                "default_variant": "a",
                "variants": [
                    {"id": "a", "title_patterns": [], "resume_path": "x.md"},
                    {"id": "a", "title_patterns": [], "resume_path": "y.md"},
                ],
            }), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(p)

    def test_unknown_default_raises(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "r.json"
            p.write_text(json.dumps({
                "schema_version": 1,
                "default_variant": "missing",
                "variants": [{"id": "a", "title_patterns": [], "resume_path": "x.md"}],
            }), encoding="utf-8")
            with self.assertRaises(RegistryError):
                load_registry(p)


class InferSeniorityTest(unittest.TestCase):
    def test_bands(self) -> None:
        self.assertEqual(infer_seniority("Staff Software Engineer"), "staff")
        self.assertEqual(infer_seniority("Senior Backend Engineer"), "senior")
        self.assertEqual(infer_seniority("Junior Developer"), "junior")
        self.assertEqual(infer_seniority("Software Engineer"), "mid")


class RouteLeadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = load_registry()

    def test_specialized_match_wins(self) -> None:
        d = route_lead(_scored_lead("Senior AI Engineer", ["python", "llm"]), self.reg)
        self.assertEqual(d["selected_variant_id"], "ai_engineer")
        self.assertFalse(d["fallback_used"])
        self.assertEqual(d["confidence"], "high")
        self.assertIn("ai engineer", d["matched_title_patterns"])

    def test_fallback_to_default_when_no_match(self) -> None:
        d = route_lead(_scored_lead("Widget Analyst", []), self.reg)
        self.assertEqual(d["selected_variant_id"], self.reg["default_variant"])
        self.assertTrue(d["fallback_used"])
        self.assertTrue(d["needs_human_review"])

    def test_near_tie_flagged(self) -> None:
        d = route_lead(
            _scored_lead("Full Stack Platform Engineer", ["react", "aws", "kubernetes"]),
            self.reg,
        )
        # Two specialized lanes match the title -> near tie -> review.
        self.assertTrue(d["needs_human_review"])
        self.assertTrue(any("near_tie" in r for r in d["review_reasons"]))

    def test_missing_resume_flags_review(self) -> None:
        d = route_lead(_scored_lead("Senior AI Engineer", ["python", "llm"]), self.reg)
        # ai-engineer resume file is not authored yet in the repo.
        self.assertFalse(d["selected_resume_exists"])
        self.assertTrue(any("resume_source_missing" in r for r in d["review_reasons"]))

    def test_unscored_lead_flagged(self) -> None:
        lead = {"lead_id": "x", "title": "Senior AI Engineer"}
        d = route_lead(lead, self.reg)
        self.assertIn("lead_not_scored", d["review_reasons"])

    def test_decision_matches_schema(self) -> None:
        d = route_lead(_scored_lead("Staff Platform Engineer", ["aws", "go"]), self.reg)
        validate(d, SELECTION_SCHEMA)

    def test_alternatives_exclude_selected(self) -> None:
        d = route_lead(_scored_lead("Senior AI Engineer", ["python"]), self.reg)
        alt_ids = {a["variant_id"] for a in d["alternatives"]}
        self.assertNotIn(d["selected_variant_id"], alt_ids)


class PickRegistryResumeTest(unittest.TestCase):
    def test_default_lane_resolves_existing_file(self) -> None:
        # Generic title -> default lane -> bundled example resume (exists).
        path, decision = pick_registry_resume(_scored_lead("Widget Analyst", []))
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertEqual(decision["selected_variant_id"], "generalist_swe")

    def test_specialized_missing_file_falls_through(self) -> None:
        # AI title routes to ai_engineer whose file is absent -> (None, decision).
        path, decision = pick_registry_resume(_scored_lead("Senior AI Engineer", ["llm"]))
        self.assertIsNone(path)
        self.assertEqual(decision["selected_variant_id"], "ai_engineer")


if __name__ == "__main__":
    unittest.main()
