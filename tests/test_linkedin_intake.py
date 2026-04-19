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


class LinkedInIntakeTest(unittest.TestCase):
    def test_extract_lead_preserves_linkedin_manual_intake_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "linkedin.json"
            leads_dir = root / "leads"
            source.write_text(json.dumps({
                "origin_board": "linkedin",
                "source": "linkedin_manual",
                "company": "ExampleCo",
                "title": "Senior Engineer",
                "location": "Remote",
                "application_url": "https://boards.greenhouse.io/example/jobs/123",
                "canonical_url": "https://boards.greenhouse.io/example/jobs/123",
                "posting_url": "https://www.linkedin.com/jobs/view/999/",
                "redirect_chain": [
                    "https://www.linkedin.com/jobs/view/999/",
                    "https://boards.greenhouse.io/example/jobs/123",
                ],
                "raw_description": "Requirements: Python",
            }), encoding="utf-8")
            lead = extract_lead(source, leads_dir)
            schema = json.loads((ROOT / "schemas" / "lead.schema.json").read_text(encoding="utf-8"))
            validate(lead, schema)
            self.assertEqual(lead["origin_board"], "linkedin")
            self.assertEqual(lead["source"], "linkedin_manual")
            self.assertEqual(lead["redirect_chain"][-1], "https://boards.greenhouse.io/example/jobs/123")


if __name__ == "__main__":
    unittest.main()
