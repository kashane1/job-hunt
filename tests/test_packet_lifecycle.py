from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt import packet_review as pr
from job_hunt.application import (
    MANUAL_CLOSED_STATUSES,
    MANUAL_PACKET_STATUSES,
    PlanError,
    mark_packet_status,
)

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

# A sentinel string that stands in for generated resume/cover prose. It lives
# ONLY in the .md files and must never appear in any history/review output.
SECRET_PROSE = "CONFIDENTIAL_RESUME_SENTENCE_DO_NOT_LEAK_42"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, str):
        path.write_text(obj, encoding="utf-8")
    else:
        path.write_text(json.dumps(obj), encoding="utf-8")


def _scaffold(root: Path, *, draft_id: str, lead_id: str, company: str = "Acme",
              title: str = "Backend Engineer", requires_human_submit: bool = True) -> None:
    d = root / "applications" / draft_id
    resume_cid = f"{lead_id}-platform_backend-20260620T000000"
    cover_cid = f"{lead_id}-cover-letter-20260620T000000"
    _write(d / "status.json", {
        "schema_version": 1,
        "draft_id": draft_id,
        "lead_id": lead_id,
        "lifecycle_state": "drafted",
        "current_stage": "not_applied",
        "requires_human_submit": requires_human_submit,
        "transitions": [],
        "attempts": [],
        "events": [],
        "generated_content_ids": [resume_cid, cover_cid],
        "created_at": "2026-06-20T00:00:00+00:00",
        "updated_at": "2026-06-20T00:00:00+00:00",
    })
    _write(d / "plan.json", {
        "draft_id": draft_id,
        "lead_id": lead_id,
        "tier": "tier_1",
        "ats_check": {"status": "ok", "errors": [], "warnings": []},
        "coherence_warnings": [],
        "generated_asset_refs": {
            "resume": {"available": True, "pdf_export_status": "ready"},
            "cover_letter": {"available": True, "pdf_export_status": "ready"},
        },
    })
    _write(root / "leads" / f"{lead_id}.json", {
        "lead_id": lead_id, "company": company, "title": title,
        "fit_assessment": {"fit_score": 80, "fit_recommendation": "strong_yes"},
        "ingested_at": "2026-06-20T06:00:00+00:00",
    })
    resume_md = root / "generated" / "resumes" / f"{resume_cid}.md"
    cover_md = root / "generated" / "cover-letters" / f"{cover_cid}.md"
    # The .md files hold prose; the JSON descriptors hold metadata only.
    _write(resume_md, f"# Resume\n\n{SECRET_PROSE}\n")
    _write(cover_md, f"# Cover\n\n{SECRET_PROSE}\n")
    _write(root / "generated" / "resumes" / f"{resume_cid}.json", {
        "content_id": resume_cid, "variant_style": "platform_backend",
        "source_document_ids": ["claim:c-approved-1"],
        "output_path": str(resume_md), "pdf_path": str(resume_md).replace(".md", ".pdf"),
    })
    _write(root / "generated" / "cover-letters" / f"{cover_cid}.json", {
        "content_id": cover_cid, "lane_id": "platform_internal_tools",
        "source_document_ids": ["claim:c-approved-1"],
        "output_path": str(cover_md), "pdf_path": str(cover_md).replace(".md", ".pdf"),
    })


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Approved claims bank so a clean synthetic packet is genuinely "ready"
        # (otherwise claims_not_all_approved would flag every packet).
        self.claims = self.root / "claims-bank.json"
        _write(self.claims, {
            "schema_version": 1,
            "claims": [{"claim_id": "c-approved-1", "review_status": "approved"}],
        })

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _status(self, draft_id: str) -> dict:
        return json.loads((self.root / "applications" / draft_id / "status.json").read_text())

    def _reviews(self) -> list[dict]:
        return pr.review_packets(data_root=self.root, claims_path=self.claims, now=NOW)


class MarkManuallySubmittedTest(_Base):
    def test_records_disposition_and_keeps_human_submit(self) -> None:
        _scaffold(self.root, draft_id="d1-apply-1", lead_id="d1")
        out = mark_packet_status(
            "d1-apply-1", "manually_submitted",
            note="applied via careers page",
            submitted_url="https://acme.com/careers/123",
            data_root=self.root,
        )
        disp = out["manual_disposition"]
        self.assertEqual(disp["status"], "manually_submitted")
        self.assertEqual(len(disp["history"]), 1)
        self.assertEqual(disp["submitted_url"], "https://acme.com/careers/123")
        # Human-submit invariant: never flipped by a manual mark.
        self.assertIs(self._status("d1-apply-1")["requires_human_submit"], True)
        # An event was appended (audit trail).
        types = [e["type"] for e in self._status("d1-apply-1")["events"]]
        self.assertIn("manual_manually_submitted", types)

    def test_manually_submitted_is_a_closed_status(self) -> None:
        self.assertIn("manually_submitted", MANUAL_CLOSED_STATUSES)


class MarkSkippedTest(_Base):
    def test_skipped_excluded_from_ready_queue(self) -> None:
        _scaffold(self.root, draft_id="s1-apply-1", lead_id="s1")
        mark_packet_status("s1-apply-1", "skipped", data_root=self.root)
        reviews = self._reviews()
        rec = next(r for r in reviews if r["draft_id"] == "s1-apply-1")
        self.assertFalse(rec["ready_for_review"])
        self.assertFalse(rec["needs_attention"])
        self.assertEqual(rec["recommended_action"], "skip")


class InvalidTransitionTest(_Base):
    def test_rejected_is_terminal(self) -> None:
        _scaffold(self.root, draft_id="r1-apply-1", lead_id="r1")
        mark_packet_status("r1-apply-1", "manually_submitted", data_root=self.root)
        mark_packet_status("r1-apply-1", "rejected", data_root=self.root)
        with self.assertRaises(PlanError) as ctx:
            mark_packet_status("r1-apply-1", "manually_submitted", data_root=self.root)
        self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_illegal_jump_from_none(self) -> None:
        _scaffold(self.root, draft_id="r2-apply-1", lead_id="r2")
        # interviewing is not reachable from a fresh packet (must submit first).
        with self.assertRaises(PlanError) as ctx:
            mark_packet_status("r2-apply-1", "interviewing", data_root=self.root)
        self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")


class UnknownInputTest(_Base):
    def test_unknown_draft_id(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            mark_packet_status("does-not-exist", "reviewed", data_root=self.root)
        self.assertEqual(ctx.exception.error_code, "profile_field_missing")

    def test_unknown_status(self) -> None:
        _scaffold(self.root, draft_id="u1-apply-1", lead_id="u1")
        with self.assertRaises(PlanError) as ctx:
            mark_packet_status("u1-apply-1", "teleported", data_root=self.root)
        self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_bad_submitted_url_rejected(self) -> None:
        _scaffold(self.root, draft_id="u2-apply-1", lead_id="u2")
        with self.assertRaises(PlanError) as ctx:
            mark_packet_status("u2-apply-1", "manually_submitted",
                               submitted_url="javascript:alert(1)", data_root=self.root)
        self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_bad_follow_up_date_rejected(self) -> None:
        _scaffold(self.root, draft_id="u3-apply-1", lead_id="u3")
        with self.assertRaises(PlanError) as ctx:
            mark_packet_status("u3-apply-1", "follow_up_later",
                               next_follow_up_date="next tuesday", data_root=self.root)
        self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")


class DryRunTest(_Base):
    def test_dry_run_does_not_write(self) -> None:
        _scaffold(self.root, draft_id="dr-apply-1", lead_id="dr")
        out = mark_packet_status("dr-apply-1", "manually_submitted",
                                 dry_run=True, data_root=self.root)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["to_status"], "manually_submitted")
        # No manual_disposition was persisted.
        self.assertNotIn("manual_disposition", self._status("dr-apply-1"))


class HistoryRedactionTest(_Base):
    def test_history_never_contains_generated_prose(self) -> None:
        _scaffold(self.root, draft_id="h1-apply-1", lead_id="h1")
        mark_packet_status("h1-apply-1", "reviewed", note="looks good",
                           data_root=self.root)
        mark_packet_status("h1-apply-1", "manually_submitted",
                           submitted_url="https://acme.com/x", data_root=self.root)
        hist = pr.packet_history("h1-apply-1", data_root=self.root, claims_path=self.claims, now=NOW)
        blob = json.dumps(hist)
        self.assertTrue(hist["found"])
        self.assertNotIn(SECRET_PROSE, blob)
        # Safe metadata IS present.
        self.assertEqual(hist["manual_status"], "manually_submitted")
        self.assertEqual(len(hist["manual_timeline"]), 2)
        self.assertEqual(hist["company"], "Acme")
        self.assertIs(hist["requires_human_submit"], True)

    def test_unknown_draft_history_is_safe(self) -> None:
        hist = pr.packet_history("nope-apply-1", data_root=self.root, now=NOW)
        self.assertFalse(hist["found"])


class ReadyQueueExclusionTest(_Base):
    def test_only_open_packets_remain_ready(self) -> None:
        _scaffold(self.root, draft_id="open-apply-1", lead_id="open")
        _scaffold(self.root, draft_id="sub-apply-1", lead_id="sub")
        _scaffold(self.root, draft_id="skip-apply-1", lead_id="skip")
        mark_packet_status("sub-apply-1", "manually_submitted", data_root=self.root)
        mark_packet_status("skip-apply-1", "skipped", data_root=self.root)
        reviews = self._reviews()
        ready_ids = {r["draft_id"] for r in reviews if r["ready_for_review"]}
        self.assertEqual(ready_ids, {"open-apply-1"})
        summary = pr.summarize(reviews)
        self.assertEqual(summary["ready_for_review"], 1)
        # Closed packets are not action items.
        self.assertEqual(summary["needs_attention"], 0)


class OpenDispositionActionsTest(_Base):
    def test_reviewed_clean_packet_recommends_manual_submit(self) -> None:
        _scaffold(self.root, draft_id="rv-apply-1", lead_id="rv")
        mark_packet_status("rv-apply-1", "reviewed", data_root=self.root)
        reviews = self._reviews()
        rec = next(r for r in reviews if r["draft_id"] == "rv-apply-1")
        self.assertEqual(rec["recommended_action"], "manual_submit")
        self.assertTrue(rec["ready_for_review"])

    def test_needs_revision_is_attention(self) -> None:
        _scaffold(self.root, draft_id="nr-apply-1", lead_id="nr")
        mark_packet_status("nr-apply-1", "needs_revision", data_root=self.root)
        reviews = self._reviews()
        rec = next(r for r in reviews if r["draft_id"] == "nr-apply-1")
        self.assertEqual(rec["recommended_action"], "revise")
        self.assertTrue(rec["needs_attention"])
        self.assertFalse(rec["ready_for_review"])

    def test_follow_up_later_is_parked(self) -> None:
        _scaffold(self.root, draft_id="fl-apply-1", lead_id="fl")
        mark_packet_status("fl-apply-1", "follow_up_later",
                           next_follow_up_date="2026-07-01", data_root=self.root)
        reviews = self._reviews()
        rec = next(r for r in reviews if r["draft_id"] == "fl-apply-1")
        self.assertEqual(rec["recommended_action"], "follow_up")
        self.assertFalse(rec["ready_for_review"])
        self.assertFalse(rec["needs_attention"])


class SafetyInvariantTest(_Base):
    def test_safety_error_survives_manual_mark(self) -> None:
        # requires_human_submit missing => safety_error; a manual mark must not hide it.
        _scaffold(self.root, draft_id="sf-apply-1", lead_id="sf",
                  requires_human_submit=False)
        mark_packet_status("sf-apply-1", "manually_submitted", data_root=self.root)
        reviews = self._reviews()
        rec = next(r for r in reviews if r["draft_id"] == "sf-apply-1")
        self.assertTrue(rec["safety_error"])
        self.assertEqual(rec["recommended_action"], "hold_safety")
        self.assertTrue(rec["needs_attention"])
        self.assertIn("missing_human_submit_flag", rec["attention_reasons"])


class VocabularyTest(unittest.TestCase):
    def test_requested_statuses_supported(self) -> None:
        for s in ("reviewed", "manually_submitted", "skipped", "not_interested",
                  "needs_revision", "follow_up_later", "rejected", "interviewing"):
            self.assertIn(s, MANUAL_PACKET_STATUSES)


if __name__ == "__main__":
    unittest.main()
