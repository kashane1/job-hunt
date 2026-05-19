from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.calibration import _weakest, propose_calibration  # noqa: E402
from job_hunt.utils import write_json  # noqa: E402


def _status(root: Path, lead_id: str, current_stage: str, ts: str) -> None:
    d = root / "applications"
    d.mkdir(parents=True, exist_ok=True)
    transitions = [{"from_stage": "not_applied", "to_stage": "applied", "timestamp": ts}]
    if current_stage not in ("applied", "not_applied"):
        transitions.append(
            {"from_stage": "applied", "to_stage": current_stage, "timestamp": ts}
        )
    write_json(d / f"{lead_id}-status.json", {
        "lead_id": lead_id,
        "current_stage": current_stage,
        "transitions": transitions,
        "generated_content_ids": [],
    })


def _lead(root: Path, lead_id: str, *, fit_score, missing, company_id: str) -> None:
    d = root / "leads"
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / f"{lead_id}.json", {
        "lead_id": lead_id,
        "title": "Senior Engineer",
        "company": "ExampleCorp",
        "company_research_id": company_id,
        "fit_assessment": {
            "fit_score": fit_score,
            "matched_skills": [],
            "missing_skills": missing,
        },
        "status": "rejected",
    })


def _company(root: Path, company_id: str, remote_policy: str) -> None:
    d = root / "companies"
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / f"{company_id}.json", {
        "company_id": company_id,
        "company_name": "ExampleCorp",
        "remote_policy": remote_policy,
        "industry": "fintech",
        "stage": "series-b",
    })


class WeakestTest(unittest.TestCase):
    def test_ordering(self) -> None:
        self.assertEqual(_weakest("ok", "low"), "low")
        self.assertEqual(_weakest("ok", "ok"), "ok")
        self.assertEqual(_weakest("low", "insufficient_data"), "insufficient_data")
        self.assertEqual(_weakest(), "insufficient_data")


class ProposalGenerationTest(unittest.TestCase):
    def _seed_rejection_scenario(self, root: Path) -> None:
        """12 scored leads, all rejected at the 'applied' stage, all from
        onsite companies, all missing 'terraform' — clears every analytics
        sample-size gate and should trigger all three proposal types."""
        _company(root, "co-onsite", "onsite")
        for i in range(12):
            lid = f"lead-{i:04d}"
            _status(root, lid, "rejected", "2026-04-10T12:00:00+00:00")
            _lead(root, lid, fit_score=80, missing=["terraform"], company_id="co-onsite")

    def test_all_proposal_types_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_rejection_scenario(root)
            result = propose_calibration(
                root,
                profile={"skills": []},
                scoring_config={"skill_keywords": ["python"], "negative_keywords": []},
            )

            self.assertEqual(result["overall_confidence"], "low")
            self.assertEqual(result["apply_policy"], "manual_human_edit_only")
            keys = {(p["key"], p["change"], p["value"]) for p in result["scoring_proposals"]}
            self.assertIn(("skill_keywords", "add", "terraform"), keys)
            self.assertIn(("negative_keywords", "add", "onsite"), keys)
            raised = {p["key"] for p in result["scoring_proposals"] if p["change"] == "raise"}
            self.assertIn("strong_yes_threshold", raised)
            self.assertIn("maybe_threshold", raised)

            # threshold proposal respects the configured current value
            thr = next(p for p in result["scoring_proposals"] if p["key"] == "maybe_threshold")
            self.assertEqual(thr["current"], 55)
            self.assertEqual(thr["value"], 60)

            # evidence suggestion surfaces the recurring gap
            skills = {s["skill"] for s in result["profile_evidence_suggestions"]}
            self.assertIn("terraform", skills)

            # artifact written under data/calibration, both formats
            proposal_path = Path(result["proposal_path"])
            self.assertTrue(proposal_path.exists())
            self.assertTrue(proposal_path.with_suffix(".md").exists())
            self.assertEqual(proposal_path.parent, root / "calibration")

    def test_skill_already_a_keyword_not_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_rejection_scenario(root)
            result = propose_calibration(
                root,
                profile={"skills": []},
                scoring_config={
                    "skill_keywords": ["terraform"],  # already covered
                    "negative_keywords": ["onsite"],  # already covered
                },
            )
            keys = {(p["key"], p["value"]) for p in result["scoring_proposals"]}
            self.assertNotIn(("skill_keywords", "terraform"), keys)
            self.assertNotIn(("negative_keywords", "onsite"), keys)

    def test_insufficient_data_yields_no_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Only 3 records — below every gate.
            _company(root, "co", "remote")
            for i in range(3):
                lid = f"l-{i}"
                _status(root, lid, "rejected", "2026-04-10T12:00:00+00:00")
                _lead(root, lid, fit_score=70, missing=["terraform"], company_id="co")
            result = propose_calibration(
                root, profile={"skills": []},
                scoring_config={"skill_keywords": [], "negative_keywords": []},
            )
            self.assertEqual(result["overall_confidence"], "insufficient_data")
            self.assertEqual(result["scoring_proposals"], [])
            self.assertIn("guidance", result)
            # Still writes the (empty) proposal artifact for the audit trail.
            self.assertTrue(Path(result["proposal_path"]).exists())

    def test_does_not_mutate_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_rejection_scenario(root)
            scoring = {"skill_keywords": ["python"], "negative_keywords": []}
            profile = {"skills": []}
            propose_calibration(root, profile=profile, scoring_config=scoring)
            # propose-only: the passed-in config/profile objects are untouched.
            self.assertEqual(scoring, {"skill_keywords": ["python"], "negative_keywords": []})
            self.assertEqual(profile, {"skills": []})


if __name__ == "__main__":
    unittest.main()
