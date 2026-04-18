from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.ats_check import (
    KEYWORD_COVERAGE_ERROR_THRESHOLD,
    KEYWORD_COVERAGE_WARN_THRESHOLD,
    KEYWORD_DENSITY_STUFFING_THRESHOLD,
    RESUME_MAX_PAGES_DEFAULT,
    check_cover_letter,
    check_resume,
    run_ats_check,
    run_ats_check_with_recovery,
)
from job_hunt.schema_checks import validate
from job_hunt.utils import read_json, write_json


RESUME_TEMPLATE = """# Jane Engineer

jane@example.com | (555) 555-0100 | https://www.linkedin.com/in/jane/

## Professional Summary

Senior engineer with 5+ years building scalable data platforms and distributed
systems. Delivered 40% latency reduction on core APIs serving 10M requests per
day, migrated legacy monoliths to service-oriented architectures, and mentored
multiple junior engineers. Passionate about clean interfaces, observable systems,
and pragmatic engineering tradeoffs.

## Technical Skills

Backend languages and frameworks including TypeScript, JavaScript, and Node.js.
Data stores including Postgres, Redis, and Kafka streaming. Cloud infrastructure
on AWS with Docker containers orchestrated via Kubernetes. Strong frontend work
with React and modern component architecture. Familiar with CI/CD automation,
observability, and security best practices.

## Professional Experience

### Senior Engineer | ExampleCorp | 2022 to Present

- Built a scalable Postgres ingestion pipeline processing ten million events per
  day, with dead letter handling and observability dashboards using structured
  logging and metrics for operational insight.
- Led migration of the monolithic codebase to service-oriented architecture,
  reducing deploy time from forty-five minutes down to five minutes via parallel
  container builds and shared base images across repositories.
- Designed a caching layer cutting p99 latency from eight hundred milliseconds
  to one hundred and twenty milliseconds by introducing Redis with carefully
  tuned TTL policies and consistent hashing for partition distribution.
- Mentored three junior engineers on distributed systems fundamentals, pairing
  on tricky debugging sessions, reviewing pull requests with detailed feedback,
  and leading internal tech talks on operational excellence.

### Software Engineer | PreviousCorp | 2019 to 2022

- Shipped the public API serving thousands of external partners in TypeScript
  with OpenAPI schemas, rate limiting, and careful backward compatibility.
- Drove adoption of type hints across twelve Python microservices, improving
  static analysis coverage from forty percent to ninety percent within a year.

## Education

BS Computer Science, University of Example, 2019. Graduated with distinction;
relevant coursework included distributed systems, compilers, and databases.
"""

LEAD_TEMPLATE = {
    "lead_id": "examplecorp-senior-engineer-abc12345",
    "normalized_requirements": {
        "keywords": ["python", "postgres", "aws", "docker", "kubernetes"],
        "required": [],
        "preferred": [],
    },
}


class CheckResumeTest(unittest.TestCase):
    def test_passes_well_formed_resume(self) -> None:
        result = check_resume(RESUME_TEMPLATE, LEAD_TEMPLATE, max_pages=2)
        self.assertEqual(result["errors"], [])
        # Might have warnings for length, but no errors

    def test_flags_missing_required_section(self) -> None:
        # Remove "Education" section
        broken = RESUME_TEMPLATE.replace("## Education", "## SomethingElse")
        result = check_resume(broken, None)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("missing_required_section", codes)

    def test_flags_resume_too_short(self) -> None:
        tiny = "# Jane\n## Technical Skills\n- Python\n## Professional Experience\n- Job\n## Education\n- School"
        result = check_resume(tiny, None)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("resume_too_short", codes)

    def test_warns_when_pages_exceed_target(self) -> None:
        long_body = RESUME_TEMPLATE + ("\n- " + "lorem ipsum " * 20) * 20
        result = check_resume(long_body, None, max_pages=1)
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("resume_too_long", codes)

    def test_computes_keyword_coverage(self) -> None:
        result = check_resume(RESUME_TEMPLATE, LEAD_TEMPLATE, max_pages=2)
        self.assertIn("keyword_coverage", result["metrics"])
        self.assertGreater(result["metrics"]["keyword_coverage"], 0.5)

    def test_flags_low_keyword_coverage(self) -> None:
        # Resume has python/postgres/aws/docker/kubernetes; this lead wants 10 rust-specific skills
        rust_lead = {
            "normalized_requirements": {
                "keywords": ["rust", "tokio", "actix", "diesel", "cargo", "serde", "wasm", "axum", "sqlx", "bevy"],
                "required": [],
                "preferred": [],
            },
        }
        result = check_resume(RESUME_TEMPLATE, rust_lead, max_pages=2)
        codes = [e["code"] for e in result["errors"]]
        # 0/10 = 0% coverage — below 30% error threshold
        self.assertIn("low_keyword_coverage", codes)

    def test_flags_keyword_stuffing(self) -> None:
        # Build content that's >5% keywords
        stuffed = "# X\n## Technical Skills\npython python python python python python\n## Professional Experience\npython python python\n## Education\npython"
        # Need at least RESUME_MIN_WORDS
        stuffed += ("\n- python " * 50)
        lead = {
            "normalized_requirements": {
                "keywords": ["python"],
                "required": [],
                "preferred": [],
            },
        }
        result = check_resume(stuffed, lead)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("keyword_stuffing", codes)

    def test_coverage_without_lead_skipped(self) -> None:
        result = check_resume(RESUME_TEMPLATE, None)
        self.assertNotIn("keyword_coverage", result["metrics"])

    def test_coverage_threshold_constants_are_sane(self) -> None:
        self.assertLess(KEYWORD_COVERAGE_ERROR_THRESHOLD, KEYWORD_COVERAGE_WARN_THRESHOLD)
        self.assertLess(KEYWORD_DENSITY_STUFFING_THRESHOLD, 0.2)


class CheckCoverLetterTest(unittest.TestCase):
    def test_passes_well_formed_letter(self) -> None:
        letter = (
            "Dear Hiring Manager,\n\nI am excited to apply for the Senior Engineer role "
            "at ExampleCorp. My background in Python and Postgres aligns well with your "
            "requirements.\n\nSincerely,\nJane"
        )
        result = check_cover_letter(letter, LEAD_TEMPLATE)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["errors"], [])

    def test_warns_missing_opening(self) -> None:
        letter = "Hi there,\n\nI want the job.\n"
        result = check_cover_letter(letter, None)
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("missing_opening_salutation", codes)

    def test_warns_too_long(self) -> None:
        letter = "Dear Hiring Manager,\n" + ("word " * 500)
        result = check_cover_letter(letter, None)
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("cover_letter_too_long", codes)


LEAD_WITH_COMPANY = dict(LEAD_TEMPLATE)
LEAD_WITH_COMPANY["company"] = "ExampleCorp"


class CheckCoverLetterHardErrorsTest(unittest.TestCase):
    """Phase 3: deterministic hard-error backstops when generation skipped them."""

    def test_unresolved_placeholder_is_hard_error(self) -> None:
        letter = (
            "Dear Hiring Manager,\n\nI'd love to work at [Company] as a [Role]. "
            "\n\nSincerely,\nJane"
        )
        result = check_cover_letter(letter, LEAD_WITH_COMPANY)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("unresolved_placeholder", codes)

    def test_wrong_company_name_is_hard_error(self) -> None:
        letter = (
            "Dear Hiring Manager,\n\nI bring launch experience from SpaceX and "
            "would love to join ExampleCorp.\n\nSincerely,\nJane"
        )
        result = check_cover_letter(letter, LEAD_WITH_COMPANY)
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("wrong_company_name", codes)

    def test_wrong_company_escape_hatch_when_target_is_denylisted(self) -> None:
        lead = dict(LEAD_WITH_COMPANY)
        lead["company"] = "SpaceX"
        letter = (
            "Dear Hiring Manager,\n\nI'm excited to apply at SpaceX for this role. "
            "\n\nSincerely,\nJane"
        )
        result = check_cover_letter(letter, lead)
        codes = [e["code"] for e in result["errors"]]
        self.assertNotIn("wrong_company_name", codes)

    def test_unsupported_company_language_warning(self) -> None:
        # Letter makes an unsupported claim about ExampleCorp's mission; no
        # company_research backs "mission" → warning fires.
        letter = (
            "Dear Hiring Manager,\n\nI deeply believe in ExampleCorp's mission "
            "and want to contribute.\n\nSincerely,\nJane"
        )
        result = check_cover_letter(letter, LEAD_WITH_COMPANY, company_research=None)
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("unsupported_company_language", codes)

    def test_unsupported_company_language_silenced_when_grounded(self) -> None:
        letter = (
            "Dear Hiring Manager,\n\nExampleCorp's product in the data-engineering "
            "space is exactly the kind of work I want to be part of.\n\nSincerely,\nJane"
        )
        research = {
            "company_name": "ExampleCorp",
            "product": "Postgres-backed analytics product",
            "industry": "data engineering",
        }
        result = check_cover_letter(letter, LEAD_WITH_COMPANY, company_research=research)
        codes = [w["code"] for w in result["warnings"]]
        self.assertNotIn("unsupported_company_language", codes)

    def test_weak_evidence_density_warning(self) -> None:
        letter = "Dear Hiring Manager,\n\n" + (
            "I am writing to apply. I enjoy software. I hope to hear back. " * 20
        ) + "\n\nSincerely,\nJane"
        result = check_cover_letter(letter, LEAD_WITH_COMPANY)
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("weak_evidence_density", codes)


class RunAtsCheckTest(unittest.TestCase):
    def test_writes_report_file_and_returns_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = root / "c1.md"
            md_path.write_text(RESUME_TEMPLATE, encoding="utf-8")
            record = {
                "content_id": "c1",
                "content_type": "resume",
                "lead_id": "l1",
                "output_path": str(md_path),
            }
            report = run_ats_check(record, LEAD_TEMPLATE, root, max_pages=2)
            self.assertEqual(report["content_id"], "c1")
            self.assertIn(report["status"], ("passed", "warnings", "errors"))
            self.assertTrue((root / "c1-check.json").exists())
            # Validate against schema
            schema = json.loads((ROOT / "schemas" / "ats-check-report.schema.json").read_text())
            validate(report, schema)

    def test_raises_on_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            record = {
                "content_id": "c1",
                "content_type": "resume",
                "output_path": str(Path(tmpdir) / "nonexistent.md"),
            }
            with self.assertRaises(ValueError):
                run_ats_check(record, None, Path(tmpdir))

    def test_raises_on_missing_output_path(self) -> None:
        record = {"content_id": "c1", "content_type": "resume"}
        with self.assertRaises(ValueError):
            run_ats_check(record, None, Path("/tmp"))

    def test_cover_letter_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = root / "cl1.md"
            md_path.write_text(
                "Dear Hiring Manager,\n\nA short but valid letter.\n\nSincerely,\nJane",
                encoding="utf-8",
            )
            record = {
                "content_id": "cl1",
                "content_type": "cover_letter",
                "output_path": str(md_path),
            }
            report = run_ats_check(record, None, root)
            self.assertEqual(report["status"], "passed")


class TolerantReaderTest(unittest.TestCase):
    """Phase 0: ATS must read new optional lane/warning fields without breaking
    when they are absent (pre-lane artifacts) and surface them when present."""

    def _write_letter(self, root: Path, name: str = "cl1.md") -> Path:
        md_path = root / name
        md_path.write_text(
            "Dear Hiring Manager,\n\nA short but valid letter.\n\nSincerely,\nJane",
            encoding="utf-8",
        )
        return md_path

    def test_pre_lane_record_still_passes(self) -> None:
        """Records written before lane fields existed must still validate and pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = self._write_letter(root)
            record = {
                "content_id": "cl1",
                "content_type": "cover_letter",
                "output_path": str(md_path),
            }
            report = run_ats_check(record, None, root)
            self.assertEqual(report["status"], "passed")
            # No lane fields should appear on the report when the record lacks them.
            self.assertNotIn("lane_id", report)
            self.assertNotIn("lane_source", report)
            schema = json.loads(
                (ROOT / "schemas" / "ats-check-report.schema.json").read_text()
            )
            validate(report, schema)

    def test_generation_warnings_surface_into_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = self._write_letter(root)
            record = {
                "content_id": "cl1",
                "content_type": "cover_letter",
                "output_path": str(md_path),
                "generation_warnings": [
                    {"code": "lane_low_confidence", "severity": "warning",
                     "detail": "top-2 margin 0.02 below 0.05 threshold"},
                ],
            }
            report = run_ats_check(record, None, root)
            self.assertEqual(report["status"], "warnings")
            codes = [w["code"] for w in report["warnings"]]
            self.assertIn("lane_low_confidence", codes)
            # Schema requires {code, message}; detail maps to message.
            relayed = next(w for w in report["warnings"] if w["code"] == "lane_low_confidence")
            self.assertIn("margin", relayed["message"])
            schema = json.loads(
                (ROOT / "schemas" / "ats-check-report.schema.json").read_text()
            )
            validate(report, schema)

    def test_lane_metadata_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = self._write_letter(root)
            record = {
                "content_id": "cl1",
                "content_type": "cover_letter",
                "output_path": str(md_path),
                "lane_id": "ai_engineer",
                "lane_source": "auto",
                "lane_rationale": "strong overlap on ml / llm keywords",
            }
            report = run_ats_check(record, None, root)
            self.assertEqual(report["lane_id"], "ai_engineer")
            self.assertEqual(report["lane_source"], "auto")
            self.assertEqual(report["lane_rationale"], "strong overlap on ml / llm keywords")

    def test_empty_generation_warnings_list_is_benign(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = self._write_letter(root)
            record = {
                "content_id": "cl1",
                "content_type": "cover_letter",
                "output_path": str(md_path),
                "generation_warnings": [],
            }
            report = run_ats_check(record, None, root)
            self.assertEqual(report["status"], "passed")


class RunAtsCheckWithRecoveryTest(unittest.TestCase):
    def test_happy_path_sets_result_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = root / "c1.md"
            md_path.write_text(RESUME_TEMPLATE, encoding="utf-8")
            record_path = root / "c1.json"
            write_json(record_path, {
                "content_id": "c1",
                "content_type": "resume",
                "lead_id": "l1",
                "output_path": str(md_path),
                "generated_at": "2026-04-16T10:00:00+00:00",
                "variant_style": "technical_depth",
                "source_document_ids": [],
            })
            updated = run_ats_check_with_recovery(record_path, LEAD_TEMPLATE, root, max_pages=2)
            self.assertIn(updated["ats_check"]["status"], ("passed", "warnings"))
            self.assertIn("report_path", updated["ats_check"])
            # Verify file state matches
            reread = read_json(record_path)
            self.assertEqual(reread["ats_check"], updated["ats_check"])

    def test_check_failure_yields_check_failed_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            record_path = root / "c1.json"
            write_json(record_path, {
                "content_id": "c1",
                "content_type": "resume",
                "lead_id": "l1",
                "output_path": str(root / "nonexistent.md"),  # will cause run_ats_check to raise
                "generated_at": "2026-04-16T10:00:00+00:00",
                "variant_style": "technical_depth",
                "source_document_ids": [],
            })
            updated = run_ats_check_with_recovery(record_path, None, root)
            self.assertEqual(updated["ats_check"]["status"], "check_failed")
            self.assertIn("error", updated["ats_check"])

    def test_pending_then_final_state(self) -> None:
        """Verify the two-phase write actually persists 'pending' before the check runs.
        We can observe this by patching run_ats_check to read the file mid-flight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            md_path = root / "c1.md"
            md_path.write_text(RESUME_TEMPLATE, encoding="utf-8")
            record_path = root / "c1.json"
            write_json(record_path, {
                "content_id": "c1",
                "content_type": "resume",
                "lead_id": "l1",
                "output_path": str(md_path),
                "generated_at": "2026-04-16T10:00:00+00:00",
                "variant_style": "technical_depth",
                "source_document_ids": [],
            })

            observed: dict = {}

            def peek_and_delegate(record, lead, output_dir, max_pages=1, company_research=None):
                # Mid-flight read — the record file should currently show "pending"
                mid = read_json(record_path)
                observed["mid_status"] = mid["ats_check"]["status"]
                # Delegate to real run_ats_check
                from job_hunt.ats_check import run_ats_check as real_run
                return real_run(
                    record, lead, output_dir, max_pages=max_pages,
                    company_research=company_research,
                )

            with patch("job_hunt.ats_check.run_ats_check", side_effect=peek_and_delegate):
                run_ats_check_with_recovery(record_path, LEAD_TEMPLATE, root, max_pages=2)

            self.assertEqual(observed["mid_status"], "pending")


class GeneratedContentBackwardCompatTest(unittest.TestCase):
    def test_ats_check_absent_on_old_records_doesnt_crash(self) -> None:
        """A batch-1 content record without ats_check should still validate."""
        schema = json.loads((ROOT / "schemas" / "generated-content.schema.json").read_text())
        old_record = {
            "content_id": "old-c1",
            "content_type": "resume",
            "variant_style": "technical_depth",
            "generated_at": "2026-04-15T10:00:00+00:00",
            "lead_id": "old-lead",
            "source_document_ids": ["doc-1"],
        }
        validate(old_record, schema)  # should not raise

    def test_records_with_ats_check_also_validate(self) -> None:
        schema = json.loads((ROOT / "schemas" / "generated-content.schema.json").read_text())
        new_record = {
            "content_id": "new-c1",
            "content_type": "resume",
            "variant_style": "technical_depth",
            "generated_at": "2026-04-16T10:00:00+00:00",
            "lead_id": "new-lead",
            "source_document_ids": ["doc-1"],
            "ats_check": {
                "status": "passed",
                "report_path": "data/generated/ats-checks/new-c1-check.json",
                "checked_at": "2026-04-16T10:00:01+00:00",
            },
            "pdf_path": "data/generated/resumes/new-c1.pdf",
            "pdf_generated_at": "2026-04-16T10:00:02+00:00",
        }
        validate(new_record, schema)


if __name__ == "__main__":
    unittest.main()
