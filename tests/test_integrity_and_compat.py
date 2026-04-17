"""Batch 3 integration + backward-compat tests.

- Batch-1 leads (no discovered_via / canonical_url / ingested_at) still pass
  lead.schema.json validation and every downstream reader.
- check-integrity detects all four new orphan types (stale review, stale
  .tmp, unscored discovered lead).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.schema_checks import validate
from job_hunt.tracking import check_integrity


LEAD_SCHEMA = json.loads((ROOT / "schemas" / "lead.schema.json").read_text())


class BackwardCompatLeadSchemaTest(unittest.TestCase):
    def test_legacy_lead_without_new_fields_validates(self) -> None:
        legacy = {
            "lead_id": "legacy-1",
            "fingerprint": "abc",
            "source": "manual",
            "application_url": "https://example.com/jobs/1",
            "company": "Legacy Co",
            "title": "Senior Engineer",
            "location": "Remote",
            "raw_description": "desc",
            "normalized_requirements": {
                "required": ["python"],
                "preferred": [],
                "keywords": ["engineer"],
            },
            "status": "discovered",
        }
        validate(legacy, LEAD_SCHEMA)

    def test_lead_with_discovered_via_validates(self) -> None:
        lead = {
            "lead_id": "new-1",
            "fingerprint": "abc",
            "source": "greenhouse",
            "application_url": "https://boards.greenhouse.io/co/jobs/1",
            "company": "NewCo",
            "title": "Staff Engineer",
            "location": "Remote",
            "raw_description": "desc",
            "normalized_requirements": {
                "required": [], "preferred": [], "keywords": [],
            },
            "status": "discovered",
            "discovered_via": [{
                "source": "greenhouse_board",
                "company": "NewCo",
                "discovered_at": "2026-04-16T00:00:00Z",
                "listing_updated_at": "2026-04-10T00:00:00Z",
                "confidence": "high",
            }],
        }
        validate(lead, LEAD_SCHEMA)


class CheckIntegrityBatch3Test(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        (self.data_root / "leads").mkdir(parents=True)
        (self.data_root / "discovery" / "review").mkdir(parents=True)

    def _seed_stale_review(self) -> Path:
        review_path = self.data_root / "discovery" / "review" / ("a" * 16 + ".md")
        review_path.write_text("---\nentry_id: aaaa\n---\n", encoding="utf-8")
        # Age the file to > 30 days
        old_time = time.time() - 31 * 24 * 3600
        os.utime(review_path, (old_time, old_time))
        return review_path

    def _seed_stale_tmp(self) -> Path:
        tmp_path = self.data_root / "leads" / ".staging.tmp"
        tmp_path.write_text("garbage", encoding="utf-8")
        old_time = time.time() - 2 * 3600
        os.utime(tmp_path, (old_time, old_time))
        return tmp_path

    def _seed_unscored_discovered_lead(self) -> Path:
        lead_path = self.data_root / "leads" / "orphan.json"
        lead_path.write_text(json.dumps({
            "lead_id": "orphan",
            "fingerprint": "x",
            "source": "greenhouse",
            "application_url": "https://boards.greenhouse.io/co/jobs/1",
            "company": "X",
            "title": "Engineer",
            "location": "Remote",
            "raw_description": "",
            "normalized_requirements": {"required": [], "preferred": [], "keywords": []},
            "status": "discovered",
        }), encoding="utf-8")
        old_time = time.time() - 2 * 3600
        os.utime(lead_path, (old_time, old_time))
        return lead_path

    def test_detects_stale_review(self) -> None:
        self._seed_stale_review()
        report = check_integrity(self.data_root)
        self.assertEqual(len(report["stale_review_entries"]), 1)

    def test_detects_stale_tmp(self) -> None:
        self._seed_stale_tmp()
        report = check_integrity(self.data_root)
        self.assertEqual(len(report["stale_tmp_files"]), 1)

    def test_detects_unscored_discovered_lead(self) -> None:
        self._seed_unscored_discovered_lead()
        report = check_integrity(self.data_root)
        self.assertEqual(len(report["unscored_discovered_leads"]), 1)
        self.assertEqual(
            report["unscored_discovered_leads"][0]["lead_id"],
            "orphan",
        )

    def test_summary_reports_issues(self) -> None:
        self._seed_stale_review()
        self._seed_unscored_discovered_lead()
        report = check_integrity(self.data_root)
        self.assertTrue(report["summary"]["has_issues"])
        self.assertEqual(
            report["summary"]["issue_counts"]["stale_review_entries"], 1,
        )


class DiscoveryErrorCodeCoverageTest(unittest.TestCase):
    def test_every_raise_in_discovery_uses_frozen_code(self) -> None:
        """Static check: every `error_code=...` literal in discovery.py
        is a member of DISCOVERY_ERROR_CODES. Prevents drift between the
        frozen set and the raise sites."""
        import re as re_mod
        from job_hunt.discovery import DISCOVERY_ERROR_CODES
        text = (ROOT / "src" / "job_hunt" / "discovery.py").read_text(encoding="utf-8")
        # Only literals passed as keyword arg `error_code="..."` to DiscoveryError
        for m in re_mod.finditer(
            r'DiscoveryError\([^)]*?error_code\s*=\s*"([^"]+)"',
            text,
            re_mod.S,
        ):
            self.assertIn(m.group(1), DISCOVERY_ERROR_CODES)


if __name__ == "__main__":
    unittest.main()
