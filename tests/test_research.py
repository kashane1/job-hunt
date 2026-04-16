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

from job_hunt.research import research_company, research_company_from_lead, score_company_fit
from job_hunt.schema_checks import validate
from job_hunt.utils import read_json


class ResearchTest(unittest.TestCase):
    def _schema(self) -> dict:
        return json.loads(
            (ROOT / "schemas" / "company-research.schema.json").read_text(encoding="utf-8")
        )

    def test_research_company_creates_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            result = research_company("ExampleCo", d)
            self.assertEqual(result["company_name"], "ExampleCo")
            self.assertEqual(result["company_id"], "exampleco")
            self.assertTrue(result["researched_at"])
            validate(result, self._schema())
            # File exists.
            self.assertTrue((d / "exampleco.json").exists())

    def test_research_from_lead_extracts_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            lead = {
                "company": "TestCorp",
                "title": "Backend Engineer",
                "location": "San Francisco, CA",
                "raw_description": "We are a fully remote team building great products.",
                "normalized_requirements": {
                    "required": ["python", "postgres"],
                    "preferred": ["redis"],
                    "keywords": ["python", "postgres", "redis"],
                },
            }
            result = research_company_from_lead(lead, d)
            self.assertEqual(result["company_name"], "TestCorp")
            self.assertEqual(result["remote_policy"], "remote")
            self.assertIn("python", result["tech_stack"])
            self.assertEqual(result["headquarters"], "San Francisco, CA")

    def test_company_fit_scoring(self) -> None:
        company = {
            "company_id": "testco",
            "company_name": "TestCo",
            "remote_policy": "remote",
            "tech_stack": ["Python", "AWS", "Postgres"],
            "industry": "SaaS",
            "size_estimate": "100-500",
        }
        profile = {
            "skills": [
                {"name": "Python", "source_document_ids": []},
                {"name": "AWS", "source_document_ids": []},
                {"name": "React", "source_document_ids": []},
            ],
            "preferences": {
                "remote_preference": "remote",
                "preferred_industries": ["saas"],
            },
        }
        result = score_company_fit(company, profile)
        self.assertIn("company_fit_score", result)
        self.assertIn("company_fit_breakdown", result)
        # Remote match should be full.
        self.assertEqual(result["company_fit_breakdown"]["remote_match"], 30)
        # Industry match should be full.
        self.assertEqual(result["company_fit_breakdown"]["industry_match"], 20)
        # Score should be above 50.
        self.assertGreater(result["company_fit_score"], 50)

    def test_company_fit_partial_data(self) -> None:
        company = {
            "company_id": "sparse",
            "company_name": "SparseCo",
            "remote_policy": "",
            "tech_stack": [],
            "industry": "",
            "size_estimate": "",
        }
        profile = {
            "skills": [{"name": "Python", "source_document_ids": []}],
            "preferences": {"remote_preference": "remote"},
        }
        result = score_company_fit(company, profile)
        # With missing data, should still return a score (neutral defaults).
        self.assertIn("company_fit_score", result)
        self.assertGreater(result["company_fit_score"], 0)


if __name__ == "__main__":
    unittest.main()
