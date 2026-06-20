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

    def test_generalist_swe_registered_ready_local_private(self) -> None:
        # The default lane is a ready_local lane backed by a private, gitignored
        # resume (metadata only — no file existence / no private content here).
        reg = load_registry()
        v = next(x for x in reg["variants"] if x["id"] == "generalist_swe")
        self.assertEqual(v.get("review_status"), "ready_local")
        self.assertTrue(v["resume_path"].startswith("profile/resumes/"))
        self.assertNotIn("examples/", v["resume_path"])  # no committed example anchor

    def test_fullstack_product_registered_ready_local_private(self) -> None:
        # Product/full-stack lane backed by a private, gitignored resume
        # (metadata only — no file existence / no private content here).
        reg = load_registry()
        v = next(x for x in reg["variants"] if x["id"] == "fullstack_product")
        self.assertEqual(v.get("review_status"), "ready_local")
        self.assertEqual(v["resume_path"], "profile/resumes/fullstack-product.md")
        self.assertNotIn("examples/", v["resume_path"])

    def test_ai_engineer_registered_ready_local_private(self) -> None:
        # Applied-AI lane backed by a private, gitignored resume (metadata only).
        reg = load_registry()
        v = next(x for x in reg["variants"] if x["id"] == "ai_engineer")
        self.assertEqual(v.get("review_status"), "ready_local")
        self.assertEqual(v["resume_path"], "profile/resumes/ai-engineer.md")
        self.assertNotIn("examples/", v["resume_path"])

    def test_all_repo_lanes_are_ready_local(self) -> None:
        # No no_ready_lane buckets remain: every registry variant is ready_local.
        reg = load_registry()
        for v in reg["variants"]:
            self.assertEqual(v.get("review_status"), "ready_local", v["id"])

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
        # Env-robust: route against a synthetic registry whose matched lane
        # points at a guaranteed-missing file (the real repo lanes are all
        # authored locally now, so we can't rely on one being absent).
        reg = {
            "schema_version": 1,
            "default_variant": "d",
            "variants": [
                {"id": "spec", "title_patterns": ["ai engineer"],
                 "emphasis_skills": ["python"], "seniority_bands": ["mid", "senior", "staff"],
                 "resume_path": "profile/resumes/__missing_spec__.md"},
                {"id": "d", "title_patterns": [],
                 "resume_path": "profile/resumes/__missing_default__.md"},
            ],
        }
        d = route_lead(_scored_lead("Senior AI Engineer", ["python"]), reg)
        self.assertEqual(d["selected_variant_id"], "spec")
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
    def test_default_lane_routes_for_generic_title(self) -> None:
        # Generic title -> default lane (generalist_swe). Its resume is now a
        # private, gitignored ready_local file (like platform_backend), so the
        # path resolves only when present locally; a clean checkout lacks it.
        path, decision = pick_registry_resume(_scored_lead("Widget Analyst", []))
        self.assertEqual(decision["selected_variant_id"], "generalist_swe")
        if path is not None:
            self.assertTrue(path.exists())

    def test_specialized_lane_routes_for_ai_title(self) -> None:
        # AI title routes to ai_engineer (now a ready_local lane). Its resume is
        # private/gitignored, so the path resolves only when present locally; a
        # clean checkout lacks it and falls through to (None, decision).
        path, decision = pick_registry_resume(_scored_lead("Senior AI Engineer", ["llm"]))
        self.assertEqual(decision["selected_variant_id"], "ai_engineer")
        if path is not None:
            self.assertTrue(path.exists())

    def test_missing_lane_file_marks_resume_absent(self) -> None:
        # Env-robust: a variant pointing at a guaranteed-missing file reports
        # selected_resume_exists=False (the signal pick_registry_resume uses to
        # fall through). route_lead accepts an explicit registry.
        reg = {
            "schema_version": 1,
            "default_variant": "x",
            "variants": [{"id": "x", "title_patterns": [],
                          "resume_path": "profile/resumes/__definitely_missing__.md"}],
        }
        d = route_lead(_scored_lead("Anything", []), reg)
        self.assertEqual(d["selected_variant_id"], "x")
        self.assertFalse(d["selected_resume_exists"])


if __name__ == "__main__":
    unittest.main()
