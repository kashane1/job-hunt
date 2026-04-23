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

from job_hunt.core import extract_lead
from job_hunt.schema_checks import validate


class GlassdoorIntakeTest(unittest.TestCase):
    def test_extract_lead_normalizes_glassdoor_manual_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "glassdoor.json"
            leads_dir = root / "leads"
            source.write_text(json.dumps({
                "origin_board": "glassdoor",
                "company": "ExampleCo",
                "title": "Senior Engineer",
                "location": "Remote",
                "application_url": "https://www.glassdoor.com/job-listing/example-role",
                "canonical_url": "https://www.glassdoor.com/job-listing/example-role",
                "posting_url": "https://www.glassdoor.com/job-listing/example-role",
                "raw_description": "Requirements: Python",
            }), encoding="utf-8")
            lead = extract_lead(source, leads_dir)
            schema = json.loads((ROOT / "schemas" / "lead.schema.json").read_text(encoding="utf-8"))
            validate(lead, schema)
            self.assertEqual(lead["origin_board"], "glassdoor")
            self.assertEqual(lead["source"], "glassdoor_manual")

    def test_extract_lead_preserves_glassdoor_redirect_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "glassdoor-redirect.json"
            leads_dir = root / "leads"
            source.write_text(json.dumps({
                "origin_board": "glassdoor",
                "source": "glassdoor_manual",
                "company": "ExampleCo",
                "title": "Senior Engineer",
                "location": "Remote",
                "application_url": "https://boards.greenhouse.io/example/jobs/123",
                "canonical_url": "https://boards.greenhouse.io/example/jobs/123",
                "posting_url": "https://www.glassdoor.com/job-listing/example-role",
                "redirect_chain": [
                    "https://www.glassdoor.com/job-listing/example-role",
                    "https://boards.greenhouse.io/example/jobs/123",
                ],
                "raw_description": "Requirements: Python",
            }), encoding="utf-8")
            lead = extract_lead(source, leads_dir)
            self.assertEqual(lead["origin_board"], "glassdoor")
            self.assertEqual(lead["source"], "glassdoor_manual")
            self.assertEqual(lead["redirect_chain"][-1], "https://boards.greenhouse.io/example/jobs/123")


if __name__ == "__main__":
    unittest.main()
