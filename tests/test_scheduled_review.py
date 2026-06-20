from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt import scheduled_review as sr


class ResolveMaxPacketsTest(unittest.TestCase):
    def test_none_uses_default(self) -> None:
        self.assertEqual(sr.resolve_max_packets(None), sr.DEFAULT_MAX_PACKETS)

    def test_none_uses_explicit_default(self) -> None:
        self.assertEqual(sr.resolve_max_packets(None, default=5), 5)

    def test_negative_clamps_to_zero(self) -> None:
        self.assertEqual(sr.resolve_max_packets(-3), 0)

    def test_zero_allowed(self) -> None:
        self.assertEqual(sr.resolve_max_packets(0), 0)

    def test_positive_passthrough(self) -> None:
        self.assertEqual(sr.resolve_max_packets(4), 4)

    def test_non_numeric_falls_back_to_default(self) -> None:
        self.assertEqual(sr.resolve_max_packets("nope"), sr.DEFAULT_MAX_PACKETS)


def _q(*items: dict) -> dict:
    return {"items": list(items)}


class SelectPacketCandidatesTest(unittest.TestCase):
    def test_caps_to_max_packets(self) -> None:
        q = _q(
            {"status": "packet_ready", "lead_id": "a"},
            {"status": "packet_ready", "lead_id": "b"},
            {"status": "packet_ready", "lead_id": "c"},
        )
        self.assertEqual(sr.select_packet_candidates(q, max_packets=2), ["a", "b"])

    def test_only_packet_ready_selected(self) -> None:
        q = _q(
            {"status": "needs_review", "lead_id": "x"},
            {"status": "packet_ready", "lead_id": "y"},
            {"status": "reject", "lead_id": "z"},
        )
        self.assertEqual(sr.select_packet_candidates(q, max_packets=5), ["y"])

    def test_zero_cap_selects_none(self) -> None:
        q = _q({"status": "packet_ready", "lead_id": "a"})
        self.assertEqual(sr.select_packet_candidates(q, max_packets=0), [])

    def test_skips_hidden_items(self) -> None:
        q = _q(
            {"status": "packet_ready", "lead_id": "a", "hidden": True},
            {"status": "packet_ready", "lead_id": "b"},
        )
        self.assertEqual(sr.select_packet_candidates(q, max_packets=5), ["b"])

    def test_skips_items_without_lead_id(self) -> None:
        q = _q(
            {"status": "packet_ready", "lead_id": ""},
            {"status": "packet_ready", "lead_id": "b"},
        )
        self.assertEqual(sr.select_packet_candidates(q, max_packets=5), ["b"])


class GeneratedPdfSummaryTest(unittest.TestCase):
    def test_counts_ready_failed_pending(self) -> None:
        reviews = [
            {"pdf": {"overall": "ready"}},
            {"pdf": {"overall": "ready"}},
            {"pdf": {"overall": "failed"}},
            {"pdf": {"overall": "not_attempted"}},
            {"pdf": {}},
        ]
        s = sr.generated_pdf_summary(reviews)
        self.assertEqual(s, {"prepared": 5, "pdf_ready": 2, "pdf_failed": 1, "pdf_pending": 2})

    def test_empty(self) -> None:
        self.assertEqual(
            sr.generated_pdf_summary([]),
            {"prepared": 0, "pdf_ready": 0, "pdf_failed": 0, "pdf_pending": 0},
        )


class DoctorGuardrailTest(unittest.TestCase):
    def test_clean(self) -> None:
        v = sr.evaluate_doctor_guardrail(errors=0, warnings=0)
        self.assertEqual(v["status"], sr.GUARDRAIL_OK)

    def test_errors_fail(self) -> None:
        v = sr.evaluate_doctor_guardrail(errors=2, warnings=0)
        self.assertEqual(v["status"], sr.GUARDRAIL_FAIL)

    def test_warnings_warn_by_default(self) -> None:
        v = sr.evaluate_doctor_guardrail(errors=0, warnings=3)
        self.assertEqual(v["status"], sr.GUARDRAIL_WARN)

    def test_warnings_fail_under_strict(self) -> None:
        v = sr.evaluate_doctor_guardrail(errors=0, warnings=3, strict=True)
        self.assertEqual(v["status"], sr.GUARDRAIL_FAIL)


class GitignoreGuardrailTest(unittest.TestCase):
    def test_all_ignored_ok(self) -> None:
        v = sr.evaluate_gitignore_guardrail({"a": True, "b": True})
        self.assertEqual(v["status"], sr.GUARDRAIL_OK)

    def test_leak_fails_strict(self) -> None:
        v = sr.evaluate_gitignore_guardrail({"a": True, "secret.json": False})
        self.assertEqual(v["status"], sr.GUARDRAIL_FAIL)
        self.assertIn("secret.json", v["detail"])

    def test_leak_warns_when_not_strict(self) -> None:
        v = sr.evaluate_gitignore_guardrail({"secret.json": False}, strict=False)
        self.assertEqual(v["status"], sr.GUARDRAIL_WARN)

    def test_empty_map_warns(self) -> None:
        v = sr.evaluate_gitignore_guardrail({})
        self.assertEqual(v["status"], sr.GUARDRAIL_WARN)


class PdfGuardrailTest(unittest.TestCase):
    def test_no_packets_ok(self) -> None:
        v = sr.evaluate_pdf_guardrail({"prepared": 0, "pdf_failed": 0})
        self.assertEqual(v["status"], sr.GUARDRAIL_OK)

    def test_failure_warns_by_default(self) -> None:
        v = sr.evaluate_pdf_guardrail({"prepared": 2, "pdf_failed": 1, "pdf_ready": 1})
        self.assertEqual(v["status"], sr.GUARDRAIL_WARN)

    def test_failure_fails_under_strict(self) -> None:
        v = sr.evaluate_pdf_guardrail({"prepared": 2, "pdf_failed": 1}, strict=True)
        self.assertEqual(v["status"], sr.GUARDRAIL_FAIL)

    def test_all_ready_ok(self) -> None:
        v = sr.evaluate_pdf_guardrail({"prepared": 2, "pdf_failed": 0, "pdf_ready": 2})
        self.assertEqual(v["status"], sr.GUARDRAIL_OK)


class OverallStatusTest(unittest.TestCase):
    def test_fail_wins(self) -> None:
        gs = [{"status": "ok"}, {"status": "warn"}, {"status": "fail"}]
        self.assertEqual(sr.overall_guardrail_status(gs), sr.GUARDRAIL_FAIL)

    def test_warn_over_ok(self) -> None:
        gs = [{"status": "ok"}, {"status": "warn"}]
        self.assertEqual(sr.overall_guardrail_status(gs), sr.GUARDRAIL_WARN)

    def test_all_ok(self) -> None:
        gs = [{"status": "ok"}, {"status": "ok"}]
        self.assertEqual(sr.overall_guardrail_status(gs), sr.GUARDRAIL_OK)


class DiscoveryBriefTest(unittest.TestCase):
    def test_none_is_offline(self) -> None:
        self.assertEqual(sr.discovery_brief(None), {"ran": False, "mode": "offline"})

    def test_skipped_gap(self) -> None:
        b = sr.discovery_brief({"status": "skipped_gap", "error_code": "watchlist_missing"})
        self.assertFalse(b["ran"])
        self.assertEqual(b["mode"], "skipped_gap")
        self.assertEqual(b["error_code"], "watchlist_missing")

    def test_ran_summarizes_counts_and_sources(self) -> None:
        disc = {
            "counts": {"discovered": 4, "already_known": 7, "dropped_by_url_guard": 2},
            "sources_run": [
                {"source": "greenhouse", "company": "x"},
                {"source": "greenhouse", "company": "y"},
                {"source": "lever", "company": "z"},
            ],
        }
        b = sr.discovery_brief(disc)
        self.assertTrue(b["ran"])
        self.assertEqual(b["sources_contacted"], ["greenhouse", "lever"])
        self.assertEqual(b["companies_contacted"], 3)
        self.assertEqual(b["newly_ingested"], 4)
        self.assertEqual(b["dropped_by_url_guard"], 2)


class NextHumanActionTest(unittest.TestCase):
    BASE = dict(
        queue_counts={"packet_ready": 0, "needs_review": 0},
        review_summary={"needs_attention": 0},
        max_packets=2,
        packets_review_cmd="cmd review",
    )

    def test_failing_guardrail_wins(self) -> None:
        a = sr.next_human_action(guardrail_status="fail", generated_count=3, **self.BASE)
        self.assertEqual(a["kind"], "resolve_guardrail")
        self.assertIsNone(a["command"])

    def test_generated_packets_point_to_review(self) -> None:
        a = sr.next_human_action(guardrail_status="ok", generated_count=2, **self.BASE)
        self.assertEqual(a["kind"], "review_generated_packets")
        self.assertEqual(a["command"], "cmd review")

    def test_ready_held_back_by_zero_cap(self) -> None:
        kw = {**self.BASE, "queue_counts": {"packet_ready": 3, "needs_review": 0},
              "max_packets": 0}
        a = sr.next_human_action(guardrail_status="ok", generated_count=0, **kw)
        self.assertEqual(a["kind"], "raise_cap")

    def test_existing_attention(self) -> None:
        kw = {**self.BASE, "review_summary": {"needs_attention": 2}}
        a = sr.next_human_action(guardrail_status="warn", generated_count=0, **kw)
        self.assertEqual(a["kind"], "resolve_attention")
        self.assertTrue(a["command"].endswith("--needs-attention"))

    def test_needs_review_leads(self) -> None:
        kw = {**self.BASE, "queue_counts": {"packet_ready": 0, "needs_review": 5}}
        a = sr.next_human_action(guardrail_status="ok", generated_count=0, **kw)
        self.assertEqual(a["kind"], "review_leads")

    def test_nothing_actionable(self) -> None:
        a = sr.next_human_action(guardrail_status="ok", generated_count=0, **self.BASE)
        self.assertEqual(a["kind"], "widen_or_discover")


class BuildReportTest(unittest.TestCase):
    def _report(self, **over):
        kw = dict(
            since_hours=12,
            max_packets=2,
            dry_run=False,
            generated_at="2026-06-20T00:00:00+00:00",
            discovery={"counts": {"discovered": 1}, "sources_run": [{"source": "greenhouse"}]},
            queue={"totals": {"packet_ready": 1, "needs_review": 2, "reject": 3},
                   "items": [], "leads_considered": 6, "dropped_stale": 4,
                   "already_packeted": 1},
            generated=[{"status": "prepared", "lead_id": "a", "draft_id": "d1"}],
            generated_pdf={"prepared": 1, "pdf_ready": 1, "pdf_failed": 0, "pdf_pending": 0},
            review_summary={"total": 7, "ready_for_review": 6, "needs_attention": 1,
                            "safety_errors": 0},
            guardrails=[{"check": "profile_doctor", "status": "ok", "detail": "clean"}],
            top_rows=[{"company": "Acme", "title": "Engineer", "selected_lane": "x",
                       "score": 80, "status": "packet_ready"}],
            next_action={"kind": "review_generated_packets", "message": "m", "command": "c"},
        )
        kw.update(over)
        return sr.build_report(**kw)

    def test_assembles_all_sections(self) -> None:
        r = self._report()
        self.assertEqual(r["kind"], "scheduled_review")
        self.assertEqual(r["window"]["since_hours"], 12)
        self.assertEqual(r["window"]["source_mode"], "discovery")
        self.assertEqual(r["queue"]["packet_ready"], 1)
        self.assertEqual(r["queue"]["dropped_stale"], 4)
        self.assertEqual(r["packets_generated"]["count"], 1)
        self.assertEqual(r["packets_generated"]["cap"], 2)
        self.assertEqual(r["packets_generated"]["pdf"]["pdf_ready"], 1)
        self.assertEqual(r["packet_queue"]["ready_for_review"], 6)
        self.assertEqual(r["guardrail_status"], "ok")
        self.assertEqual(len(r["top_packets"]), 1)
        self.assertEqual(r["top_packets"][0]["company"], "Acme")
        self.assertEqual(r["next_action"]["kind"], "review_generated_packets")

    def test_offline_source_mode(self) -> None:
        r = self._report(discovery=None)
        self.assertEqual(r["window"]["source_mode"], "offline")
        self.assertFalse(r["discovery"]["ran"])

    def test_guardrail_status_reflects_worst(self) -> None:
        r = self._report(guardrails=[{"status": "ok"}, {"status": "fail"}])
        self.assertEqual(r["guardrail_status"], "fail")

    def test_top_rows_are_metadata_only(self) -> None:
        r = self._report()
        row = r["top_packets"][0]
        # Only safe, public metadata keys — no prose fields.
        self.assertEqual(set(row), {"company", "title", "lane", "score", "status"})

    def test_safety_footer_mentions_cap(self) -> None:
        r = self._report(max_packets=2)
        self.assertTrue(any("hard-capped at 2" in s for s in r["safety"]))


if __name__ == "__main__":
    unittest.main()
