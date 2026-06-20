"""Phase 1b tests for ``src/job_hunt/application.py``.

Covers: error-enum invariants, auto-submit invariant, schemas-list /
schemas-show helpers, preflight stub report shape.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.application import (
    APPLICATION_ERROR_CODES,
    ApplicationError,
    PLAN_ERROR_CODES,
    PlanError,
    assert_auto_submit_invariant,
    list_schemas,
    load_schema,
    run_preflight,
)
from job_hunt.schema_checks import ValidationError, validate
from job_hunt.utils import StructuredError


class EnsurePdfAssetTest(unittest.TestCase):
    """_ensure_pdf_asset records success or a clear, structured failure reason."""

    def setUp(self) -> None:
        import json
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.record_path = self.root / "rec.json"
        self.record_path.write_text(
            json.dumps({"content_id": "x", "output_path": str(self.root / "x.md")}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_already_ready_short_circuits(self) -> None:
        from job_hunt.application import _ensure_pdf_asset
        rec = {"content_id": "x", "pdf_path": "/already/x.pdf"}
        # No export attempted; returned as-is.
        out = _ensure_pdf_asset(self.record_path, rec)
        self.assertEqual(out["pdf_path"], "/already/x.pdf")
        self.assertNotIn("pdf_export_error_code", out)

    def test_success_records_pdf_path_no_error(self) -> None:
        import job_hunt.pdf_export as pe
        from job_hunt.application import _ensure_pdf_asset

        def fake_export(path):
            return {"content_id": "x", "pdf_path": str(self.root / "x.pdf")}

        orig = pe.export_pdf
        pe.export_pdf = fake_export
        try:
            out = _ensure_pdf_asset(self.record_path, {"content_id": "x"})
        finally:
            pe.export_pdf = orig
        self.assertTrue(out["pdf_path"].endswith("x.pdf"))
        self.assertNotIn("pdf_export_error_code", out)

    def test_failure_records_clear_reason_and_remediation(self) -> None:
        import json
        import job_hunt.pdf_export as pe
        from job_hunt.application import _ensure_pdf_asset
        from job_hunt.pdf_export import PdfExportError

        def fake_export(path):
            raise PdfExportError(
                "weasyprint is not installed",
                error_code="weasyprint_missing",
                remediation="pip install 'job-hunt[pdf]'",
            )

        orig = pe.export_pdf
        pe.export_pdf = fake_export
        try:
            out = _ensure_pdf_asset(self.record_path, {"content_id": "x"})
        finally:
            pe.export_pdf = orig
        self.assertEqual(out["pdf_export_error_code"], "weasyprint_missing")
        self.assertIn("not installed", out["pdf_export_error"])
        self.assertIn("pip install", out["pdf_export_remediation"])
        # Persisted to disk (so packets-review can read the reason later).
        on_disk = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["pdf_export_error_code"], "weasyprint_missing")
        self.assertIn("pip install", on_disk["pdf_export_remediation"])


class ErrorCatalogTest(unittest.TestCase):
    def test_application_error_is_structured(self) -> None:
        exc = ApplicationError("boom", error_code="session_expired")
        self.assertIsInstance(exc, StructuredError)
        self.assertEqual(exc.error_code, "session_expired")

    def test_plan_error_is_structured(self) -> None:
        exc = PlanError("boom", error_code="draft_already_exists")
        self.assertIsInstance(exc, StructuredError)
        self.assertEqual(exc.error_code, "draft_already_exists")

    def test_application_error_rejects_unknown_code(self) -> None:
        with self.assertRaises(AssertionError):
            ApplicationError("boom", error_code="not_a_real_code")

    def test_plan_error_rejects_unknown_code(self) -> None:
        with self.assertRaises(AssertionError):
            PlanError("boom", error_code="not_a_real_code")

    def test_all_application_codes_accepted(self) -> None:
        for code in APPLICATION_ERROR_CODES:
            # Must round-trip without assertion error
            exc = ApplicationError("m", error_code=code)
            self.assertEqual(exc.error_code, code)

    def test_all_plan_codes_accepted(self) -> None:
        for code in PLAN_ERROR_CODES:
            exc = PlanError("m", error_code=code)
            self.assertEqual(exc.error_code, code)

    def test_error_code_sets_are_disjoint_in_intent(self) -> None:
        # plan_schema_invalid intentionally appears in both — pre-browser
        # validation AND record_attempt raise it. All other codes belong to
        # exactly one catalog. This guards against silent drift.
        shared = APPLICATION_ERROR_CODES & PLAN_ERROR_CODES
        self.assertEqual(shared, {"plan_schema_invalid"})


class AutoSubmitInvariantTest(unittest.TestCase):
    def test_empty_list_passes(self) -> None:
        assert_auto_submit_invariant({"apply_policy": {"auto_submit_tiers": []}})

    def test_missing_policy_passes(self) -> None:
        # A runtime policy with no apply_policy block is fine — the default
        # supplies the empty list.
        assert_auto_submit_invariant({})

    def test_non_empty_list_rejected(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            assert_auto_submit_invariant({"apply_policy": {"auto_submit_tiers": ["tier_1"]}})
        self.assertEqual(ctx.exception.error_code, "policy_loosen_attempt")

    def test_non_dict_policy_passes_silently(self) -> None:
        # Defensive: callers pass merged policies that may, due to bad YAML,
        # contain None at the apply_policy key. We treat that as "no override"
        # rather than crashing inside the invariant check.
        assert_auto_submit_invariant({"apply_policy": None})


class SchemaIntrospectionTest(unittest.TestCase):
    def test_list_schemas_returns_all_batch4_schemas(self) -> None:
        names = {s["name"] for s in list_schemas()}
        for expected in (
            "application-plan",
            "application-attempt",
            "answer-bank",
            "application-batch-summary",
            "application-progress",
            "application-status",
        ):
            self.assertIn(expected, names, f"missing {expected}")

    def test_list_schemas_records_version_when_const(self) -> None:
        schemas = {s["name"]: s for s in list_schemas()}
        self.assertEqual(schemas["application-plan"]["version"], 1)

    def test_load_schema_returns_body(self) -> None:
        body = load_schema("application-plan")
        self.assertEqual(body["title"], "ApplicationPlan")

    def test_load_unknown_schema_raises_plan_error(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            load_schema("no-such-schema")
        self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_load_schema_rejects_malicious_name(self) -> None:
        # Path traversal must not escape schemas/.
        with self.assertRaises(PlanError):
            load_schema("../config/domain-allowlist")
        with self.assertRaises(PlanError):
            load_schema("invalid_underscore")
        with self.assertRaises(PlanError):
            load_schema("UPPERCASE")


class PreflightReportShapeTest(unittest.TestCase):
    def test_returns_stable_shape(self) -> None:
        report = run_preflight({"apply_policy": {"auto_submit_tiers": []}})
        self.assertIn("ok", report)
        self.assertIn("status", report)
        self.assertIn("checks", report)
        self.assertIsInstance(report["checks"], list)
        for check in report["checks"]:
            self.assertIn("name", check)
            self.assertIn("ok", check)

    def test_reports_invariant_breach(self) -> None:
        report = run_preflight({"apply_policy": {"auto_submit_tiers": ["tier_1"]}})
        checks = {c["name"]: c for c in report["checks"]}
        self.assertFalse(checks["auto_submit_tiers_empty_invariant"]["ok"])


def _minimal_plan(**overrides: object) -> dict:
    """A sanitized plan satisfying application-plan required fields. No private
    content — placeholder identity/company only."""
    plan = {
        "schema_version": 1,
        "draft_id": "acme-backend-apply-0001",
        "lead_id": "acme-backend-0001",
        "surface": "ashby_redirect",
        "playbook_path": "playbooks/application/ashby-redirect.md",
        "correlation_keys": {
            "posting_url": "https://jobs.ashbyhq.com/acme/0001",
            "company": "Acme",
            "title": "Backend Engineer",
        },
        "profile_snapshot": {"snapshot_version": 1, "snapshot_at": "2026-06-19T00:00:00+00:00"},
        "untrusted_fetched_content": {"job_description": "Build backend systems.", "nonce": "0123456789abcdef"},
        "fields": [],
        "tier": "tier_1",
        "prepared_at": "2026-06-19T00:00:00+00:00",
    }
    plan.update(overrides)
    return plan


class PacketCoherenceSchemaTest(unittest.TestCase):
    """The application-plan schema must formally back the packet-coherence
    metadata (coherence_warnings + cover-letter lane_id)."""

    def setUp(self) -> None:
        self.schema = load_schema("application-plan")

    def _mismatch_warning(self) -> dict:
        return {
            "code": "cover_letter_lane_mismatch",
            "severity": "warning",
            "detail": "resume variant 'platform_backend' expects cover-letter lane "
                      "'platform_internal_tools' but packet cover letter is 'product_minded_engineer'",
        }

    def test_empty_coherence_warnings_validates(self) -> None:
        validate(_minimal_plan(coherence_warnings=[]), self.schema)

    def test_mismatch_warning_validates(self) -> None:
        validate(_minimal_plan(coherence_warnings=[self._mismatch_warning()]), self.schema)

    def test_handoff_context_coherence_warnings_validates(self) -> None:
        plan = _minimal_plan(
            coherence_warnings=[self._mismatch_warning()],
            handoff_context={
                "requires_human_submit": True,
                "kind": "automation_playbook",
                "coherence_warnings": [self._mismatch_warning()],
            },
        )
        validate(plan, self.schema)

    def test_cover_letter_lane_id_validates(self) -> None:
        plan = _minimal_plan(generated_asset_refs={
            "resume": {"content_id": "r1", "available": True,
                       "preferred_upload_kind": "pdf", "pdf_export_status": "ready"},
            "cover_letter": {"content_id": "c1", "available": True,
                             "generation_status": "generated",
                             "lane_id": "platform_internal_tools",
                             "preferred_upload_kind": "pdf", "pdf_export_status": "ready"},
        })
        validate(plan, self.schema)

    def test_asset_refs_without_lane_id_still_validate(self) -> None:
        # Backward compatibility: a pre-existing ref with no lane_id is fine.
        plan = _minimal_plan(generated_asset_refs={
            "resume": {"content_id": "r1", "available": True,
                       "preferred_upload_kind": "pdf", "pdf_export_status": "ready"},
            "cover_letter": {"content_id": "c1", "available": True,
                             "generation_status": "generated",
                             "preferred_upload_kind": "pdf", "pdf_export_status": "ready"},
        })
        validate(plan, self.schema)

    def test_warning_missing_required_code_fails(self) -> None:
        bad = {"severity": "warning", "detail": "lane mismatch"}
        with self.assertRaises(ValidationError):
            validate(_minimal_plan(coherence_warnings=[bad]), self.schema)

    def test_warning_bad_severity_enum_fails(self) -> None:
        bad = {"code": "cover_letter_lane_mismatch", "severity": "critical", "detail": "x"}
        with self.assertRaises(ValidationError):
            validate(_minimal_plan(coherence_warnings=[bad]), self.schema)

    def test_warning_non_string_detail_fails(self) -> None:
        bad = {"code": "cover_letter_lane_mismatch", "severity": "warning", "detail": {"a": 1}}
        with self.assertRaises(ValidationError):
            validate(_minimal_plan(coherence_warnings=[bad]), self.schema)

    def test_warning_detail_carries_no_private_content(self) -> None:
        # The contract is lane-IDs/reasons only; assert the sanitized detail has none.
        detail = self._mismatch_warning()["detail"]
        self.assertNotIn("Kashane", detail)
        self.assertNotIn("@", detail)


if __name__ == "__main__":
    unittest.main()
