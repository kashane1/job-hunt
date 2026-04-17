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
from job_hunt.utils import StructuredError


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


if __name__ == "__main__":
    unittest.main()
