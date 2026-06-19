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

from job_hunt.profile_doctor import (
    approved_claims_by_lane,
    check_lanes,
    is_private_path,
    load_claims_bank,
    template_filename,
)


def _registry(variants, default="generalist_swe"):
    return {"schema_version": 1, "default_variant": default, "variants": variants}


def _bank(claims):
    return {"schema_version": 1, "claims": claims}


def _codes(report):
    return {f["code"] for f in report["findings"]}


class IsPrivatePathTest(unittest.TestCase):
    def test_private(self) -> None:
        for p in [
            "profile/raw/accomplishments.md",
            "profile/raw/intake/old-resume.txt",
            "profile/private-review/source-map.md",
            "profile/normalized/candidate-profile.json",
            "profile/resumes/ai-engineer.md",
            "profile/claims/claims-bank.json",
            "data/leads/x.json",
            "data/applications/y/plan.json",
            "docs/reports/foo-report.md",
            "docs/reports/capability-audit-2026-06-18.md",
        ]:
            self.assertTrue(is_private_path(p), p)

    def test_not_private(self) -> None:
        for p in [
            "profile/resumes/README.md",
            "profile/resumes/templates/ai-engineer.template.md",
            "profile/claims/claims-bank.example.json",
            "profile/claims/README.md",
            "config/resume-variants.json",
            "schemas/claims-bank.schema.json",
            "examples/profile/raw/resume.md",
            "docs/guides/profile-and-resume-privacy.md",
        ]:
            self.assertFalse(is_private_path(p), p)


class ApprovedClaimsTest(unittest.TestCase):
    def test_counts_only_approved(self) -> None:
        bank = _bank([
            {"claim_id": "a", "review_status": "approved", "allowed_lanes": ["ai_engineer", "generalist_swe"]},
            {"claim_id": "b", "review_status": "draft", "allowed_lanes": ["ai_engineer"]},
            {"claim_id": "c", "review_status": "approved", "allowed_lanes": ["platform_backend"]},
        ])
        counts = approved_claims_by_lane(bank)
        self.assertEqual(counts.get("ai_engineer"), 1)
        self.assertEqual(counts.get("generalist_swe"), 1)
        self.assertEqual(counts.get("platform_backend"), 1)
        self.assertIsNone(counts.get("nonexistent"))

    def test_none_bank(self) -> None:
        self.assertEqual(approved_claims_by_lane(None), {})


class TemplateFilenameTest(unittest.TestCase):
    def test_underscores_to_hyphens(self) -> None:
        self.assertEqual(template_filename("platform_backend"), "platform-backend.template.md")


class CheckLanesTest(unittest.TestCase):
    def _root_with(self, files: list[str]) -> Path:
        d = Path(tempfile.mkdtemp())
        for rel in files:
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        return d

    def test_resume_source_missing_warns(self) -> None:
        root = self._root_with([])
        reg = _registry([{"id": "generalist_swe", "title_patterns": [], "resume_path": "profile/resumes/g.md"}])
        report = check_lanes(reg, None, root)
        self.assertIn("resume_source_missing", _codes(report))
        self.assertIn("claims_bank_absent", _codes(report))

    def test_ready_but_missing_resume_errors(self) -> None:
        root = self._root_with([])
        reg = _registry([{
            "id": "generalist_swe", "title_patterns": [],
            "resume_path": "profile/resumes/g.md", "review_status": "ready",
        }])
        report = check_lanes(reg, _bank([]), root)
        self.assertIn("ready_but_missing_resume", _codes(report))
        self.assertIn("ready_but_no_claims", _codes(report))

    def test_ready_lane_fully_backed_is_clean(self) -> None:
        root = self._root_with([
            "profile/resumes/g.md",
            "profile/resumes/templates/generalist-swe.template.md",
        ])
        reg = _registry([{
            "id": "generalist_swe", "title_patterns": [],
            "resume_path": "profile/resumes/g.md", "review_status": "ready",
        }])
        bank = _bank([{"claim_id": "a", "review_status": "approved", "allowed_lanes": ["generalist_swe"]}])
        report = check_lanes(reg, bank, root)
        errors = [f for f in report["findings"] if f["level"] == "error"]
        self.assertEqual(errors, [])
        lane = report["lanes"][0]
        self.assertTrue(lane["ready"])
        self.assertEqual(lane["approved_claims"], 1)

    def test_missing_template_warns(self) -> None:
        root = self._root_with(["profile/resumes/g.md"])
        reg = _registry([{"id": "generalist_swe", "title_patterns": [], "resume_path": "profile/resumes/g.md"}])
        report = check_lanes(reg, _bank([]), root)
        self.assertIn("missing_template", _codes(report))

    def test_default_unresolved_warns(self) -> None:
        root = self._root_with(["profile/resumes/templates/generalist-swe.template.md"])
        reg = _registry([{"id": "generalist_swe", "title_patterns": [], "resume_path": "profile/resumes/missing.md"}])
        report = check_lanes(reg, _bank([]), root)
        self.assertIn("default_unresolved", _codes(report))

    def test_no_resume_path_errors(self) -> None:
        root = self._root_with([])
        reg = _registry([{"id": "generalist_swe", "title_patterns": [], "resume_path": ""}])
        report = check_lanes(reg, _bank([]), root)
        self.assertIn("no_resume_path", _codes(report))

    def test_private_status_missing_file_is_info_not_error(self) -> None:
        # A private-status lane whose resume is absent (clean checkout) must NOT
        # error or warn resume_source_missing — absence is by design.
        root = self._root_with([])
        reg = _registry([{
            "id": "platform_backend", "title_patterns": [],
            "resume_path": "profile/resumes/platform-backend.md",
            "review_status": "needs_user_review",
        }], default="platform_backend")
        report = check_lanes(reg, _bank([]), root)
        codes = _codes(report)
        self.assertIn("private_lane", codes)
        self.assertNotIn("resume_source_missing", codes)
        self.assertNotIn("ready_but_missing_resume", codes)
        self.assertEqual([f for f in report["findings"] if f["level"] == "error"], [])

    def test_private_status_local_file_present_reports_counts(self) -> None:
        root = self._root_with(["profile/resumes/platform-backend.md"])
        reg = _registry([{
            "id": "platform_backend", "title_patterns": [],
            "resume_path": "profile/resumes/platform-backend.md",
            "review_status": "ready_local",
        }], default="platform_backend")
        bank = _bank([{"claim_id": "a", "review_status": "approved", "allowed_lanes": ["platform_backend"]}])
        report = check_lanes(reg, bank, root)
        lane = report["lanes"][0]
        self.assertTrue(lane["resume_exists"])
        self.assertEqual(lane["approved_claims"], 1)
        # ready_local + present file + approved claim -> ready computed True.
        self.assertTrue(lane["ready"])
        self.assertEqual([f for f in report["findings"] if f["level"] == "error"], [])


class LoadClaimsBankTest(unittest.TestCase):
    def test_absent_returns_none(self) -> None:
        self.assertIsNone(load_claims_bank(Path("/nonexistent/claims.json")))

    def test_loads_example(self) -> None:
        bank = load_claims_bank(ROOT / "profile" / "claims" / "claims-bank.example.json")
        self.assertIsNotNone(bank)
        self.assertEqual(bank["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
