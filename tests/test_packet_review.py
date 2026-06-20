from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt import packet_review as pr

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _scaffold(
    root: Path,
    *,
    draft_id: str,
    lead_id: str,
    company: str = "Acme",
    title: str = "Backend Engineer",
    requires_human_submit: bool = True,
    lifecycle_state: str = "drafted",
    current_stage: str = "not_applied",
    tier: str = "tier_1",
    lane: str = "technical_depth",
    fit_score: int = 80,
    ats_errors: int = 0,
    ats_warnings: int = 0,
    coherence: int = 0,
    claim_ids: tuple[str, ...] = ("c-approved-1", "c-approved-2"),
    gen_warnings: tuple[str, ...] = (),
    write_generated: bool = True,
    pdf: str = "ready",  # "ready" | "failed" | "none"
) -> None:
    """Create a synthetic packet under data_root/applications + generated."""
    data_root = root
    d = data_root / "applications" / draft_id
    resume_cid = f"{lead_id}-{lane}-20260620T000000"
    cover_cid = f"{lead_id}-cover-letter-20260620T000000"
    pdf_ref_status = {"ready": "ready", "failed": "failed", "none": "not_attempted"}[pdf]
    _write(d / "status.json", {
        "draft_id": draft_id,
        "lead_id": lead_id,
        "lifecycle_state": lifecycle_state,
        "current_stage": current_stage,
        "requires_human_submit": requires_human_submit,
        "tier": tier,
        "generated_content_ids": [resume_cid, cover_cid],
    })
    _write(d / "plan.json", {
        "draft_id": draft_id,
        "lead_id": lead_id,
        "tier": tier,
        "ats_check": {
            "status": "warnings" if (ats_errors or ats_warnings) else "ok",
            "errors": [f"e{i}" for i in range(ats_errors)],
            "warnings": [f"w{i}" for i in range(ats_warnings)],
        },
        "coherence_warnings": [f"c{i}" for i in range(coherence)],
        "generated_asset_refs": {
            "resume": {"available": write_generated, "pdf_export_status": pdf_ref_status},
            "cover_letter": {"available": write_generated, "pdf_export_status": pdf_ref_status},
        },
    })
    # Lead with public metadata + a freshness signal.
    _write(data_root / "leads" / f"{lead_id}.json", {
        "lead_id": lead_id,
        "company": company,
        "title": title,
        "fit_assessment": {"fit_score": fit_score, "fit_recommendation": "strong_yes"},
        "ingested_at": "2026-06-20T06:00:00+00:00",
        "discovered_via": [
            {"source": "greenhouse_board", "listing_updated_at": "2026-06-19T12:00:00+00:00"},
        ],
    })
    if write_generated:
        sources = [f"claim:{c}" for c in claim_ids]

        def _pdf_fields(md_path: str) -> dict:
            if pdf == "ready":
                return {"pdf_path": md_path.replace(".md", ".pdf")}
            if pdf == "failed":
                return {
                    "pdf_export_error_code": "weasyprint_missing",
                    "pdf_export_error": "weasyprint is not installed",
                    "pdf_export_remediation": "pip install 'job-hunt[pdf]'",
                }
            return {}

        resume_md = f"data/generated/resumes/{resume_cid}.md"
        cover_md = f"data/generated/cover-letters/{cover_cid}.md"
        _write(data_root / "generated" / "resumes" / f"{resume_cid}.json", {
            "content_id": resume_cid,
            "variant_style": lane,
            "source_document_ids": sources,
            "output_path": resume_md,
            **_pdf_fields(resume_md),
        })
        _write(data_root / "generated" / "cover-letters" / f"{cover_cid}.json", {
            "content_id": cover_cid,
            "lane_id": "platform_internal_tools",
            "source_document_ids": sources,
            "generation_warnings": [{"code": w} for w in gen_warnings],
            "output_path": cover_md,
            **_pdf_fields(cover_md),
        })


CLAIMS = {
    "schema_version": 1,
    "claims": [
        {"claim_id": "c-approved-1", "review_status": "approved"},
        {"claim_id": "c-approved-2", "review_status": "approved"},
        {"claim_id": "c-pending-1", "review_status": "needs_user_review"},
    ],
}


class PacketReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.claims_path = self.root / "claims.json"
        _write(self.claims_path, CLAIMS)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _review(self, **kw):
        return pr.review_packets(data_root=self.root, claims_path=self.claims_path, now=NOW, **kw)

    def test_clean_packet_is_manual_submit(self) -> None:
        _scaffold(self.root, draft_id="acme-be-apply-1", lead_id="acme-be")
        rows = self._review()
        self.assertEqual(len(rows), 1)
        p = rows[0]
        self.assertEqual(p["company"], "Acme")
        self.assertEqual(p["title"], "Backend Engineer")
        self.assertEqual(p["lane"], "technical_depth")
        self.assertEqual(p["tier"], "tier_1")
        self.assertEqual(p["score"], 80)
        self.assertTrue(p["requires_human_submit"])
        self.assertTrue(p["artifacts_present"])
        self.assertTrue(p["claims"]["all_approved"])
        self.assertEqual(p["claims"]["total"], 2)
        self.assertFalse(p["needs_attention"])
        self.assertTrue(p["ready_for_review"])
        self.assertEqual(p["recommended_action"], "manual_submit")
        # Freshness derived from listing_updated_at (24h before NOW).
        self.assertEqual(p["freshness"]["basis"], "posted_at")
        self.assertEqual(p["freshness"]["age_hours"], 24.0)
        # PDF export succeeded for both assets.
        self.assertEqual(p["pdf"]["overall"], "ready")
        self.assertEqual(p["pdf"]["resume"], "ready")
        self.assertEqual(p["pdf"]["cover_letter"], "ready")
        self.assertIsNone(p["pdf"]["error_code"])

    def test_failed_pdf_export_needs_attention(self) -> None:
        _scaffold(self.root, draft_id="nopdf-apply-1", lead_id="nopdf", pdf="failed")
        p = self._review()[0]
        self.assertEqual(p["pdf"]["overall"], "failed")
        self.assertEqual(p["pdf"]["error_code"], "weasyprint_missing")
        self.assertIn("pip install", p["pdf"]["remediation"])
        self.assertIn("pdf_export_failed", p["attention_reasons"])
        self.assertTrue(p["needs_attention"])
        self.assertFalse(p["ready_for_review"])  # failed PDF must not look ready
        self.assertEqual(p["recommended_action"], "revise")

    def test_unattempted_pdf_is_soft_note_not_ready(self) -> None:
        _scaffold(self.root, draft_id="softpdf-apply-1", lead_id="softpdf", pdf="none")
        p = self._review()[0]
        self.assertEqual(p["pdf"]["overall"], "not_attempted")
        self.assertIn("pdf_not_ready", p["notes"])
        self.assertFalse(p["needs_attention"])  # soft, not a blocker
        # Soft note downgrades a would-be manual_submit to review.
        self.assertEqual(p["recommended_action"], "review")

    def test_missing_human_submit_is_safety_error(self) -> None:
        _scaffold(self.root, draft_id="x-apply-1", lead_id="x", requires_human_submit=False)
        p = self._review()[0]
        self.assertTrue(p["safety_error"])
        self.assertIn("missing_human_submit_flag", p["attention_reasons"])
        self.assertEqual(p["recommended_action"], "hold_safety")
        self.assertFalse(p["ready_for_review"])

    def test_ats_errors_force_revise(self) -> None:
        _scaffold(self.root, draft_id="y-apply-1", lead_id="y", ats_errors=2)
        p = self._review()[0]
        self.assertEqual(p["ats"]["errors"], 2)
        self.assertIn("ats_errors", p["attention_reasons"])
        self.assertEqual(p["recommended_action"], "revise")

    def test_unapproved_claim_forces_revise(self) -> None:
        _scaffold(self.root, draft_id="z-apply-1", lead_id="z",
                  claim_ids=("c-approved-1", "c-pending-1"))
        p = self._review()[0]
        self.assertFalse(p["claims"]["all_approved"])
        self.assertIn("claims_not_all_approved", p["attention_reasons"])
        self.assertEqual(p["recommended_action"], "revise")

    def test_unsafe_prose_warning_is_soft_note_review(self) -> None:
        _scaffold(self.root, draft_id="w-apply-1", lead_id="w",
                  gen_warnings=("unsafe_prose_filtered",))
        p = self._review()[0]
        self.assertEqual(p["claim_safety_warnings"], ["unsafe_prose_filtered"])
        self.assertFalse(p["needs_attention"])  # soft note, not a blocker
        self.assertIn("claim_safety_filtered", p["notes"])
        self.assertEqual(p["recommended_action"], "review")

    def test_duplicate_lead_is_skip(self) -> None:
        _scaffold(self.root, draft_id="dup-apply-1", lead_id="dup-lead")
        _scaffold(self.root, draft_id="dup-apply-2", lead_id="dup-lead")
        rows = self._review()
        for p in rows:
            self.assertTrue(p["duplicate_of"])
            self.assertEqual(p["recommended_action"], "skip")

    def test_terminal_state_is_skip(self) -> None:
        _scaffold(self.root, draft_id="done-apply-1", lead_id="done",
                  lifecycle_state="submitted")
        p = self._review()[0]
        self.assertEqual(p["recommended_action"], "skip")
        self.assertFalse(p["ready_for_review"])

    def test_missing_artifacts_needs_attention(self) -> None:
        _scaffold(self.root, draft_id="noart-apply-1", lead_id="noart",
                  write_generated=False)
        p = self._review()[0]
        self.assertFalse(p["artifacts_present"])
        self.assertIn("missing_artifacts", p["attention_reasons"])
        self.assertEqual(p["recommended_action"], "revise")

    def test_filters_and_summary(self) -> None:
        _scaffold(self.root, draft_id="acme-apply-1", lead_id="acme-1", company="Acme",
                  lane="technical_depth", fit_score=90)
        _scaffold(self.root, draft_id="globex-apply-1", lead_id="globex-1", company="Globex",
                  lane="product_breadth", fit_score=70)
        _scaffold(self.root, draft_id="bad-apply-1", lead_id="bad-1", company="Initech",
                  lane="ai_focus", ats_errors=1)
        rows = self._review()
        self.assertEqual(len(rows), 3)
        # company filter
        self.assertEqual(len(pr.apply_filters(rows, company="globex")), 1)
        # lane filter
        self.assertEqual(len(pr.apply_filters(rows, lane="technical_depth")), 1)
        # ready-only excludes the ats-error one
        ready = pr.apply_filters(rows, ready_only=True)
        self.assertTrue(all(r["ready_for_review"] for r in ready))
        self.assertEqual(len(ready), 2)
        # needs-attention only the ats-error one
        attn = pr.apply_filters(rows, needs_attention=True)
        self.assertEqual(len(attn), 1)
        self.assertEqual(attn[0]["company"], "Initech")
        # limit
        self.assertEqual(len(pr.apply_filters(rows, limit=1)), 1)
        summ = pr.summarize(rows)
        self.assertEqual(summ["total"], 3)
        self.assertEqual(summ["needs_attention"], 1)
        self.assertEqual(summ["safety_errors"], 0)

    def test_no_claims_index_reports_unknown_not_crash(self) -> None:
        _scaffold(self.root, draft_id="nci-apply-1", lead_id="nci")
        rows = pr.review_packets(data_root=self.root, claims_path=None, now=NOW)
        p = rows[0]
        self.assertFalse(p["claims"]["all_approved"])
        self.assertEqual(p["claims"]["unknown"], 2)
        self.assertIn("claims_not_all_approved", p["attention_reasons"])

    def test_empty_data_root_is_empty(self) -> None:
        self.assertEqual(
            pr.review_packets(data_root=self.root / "nope", claims_path=None, now=NOW), []
        )

    def test_no_private_prose_keys_in_output(self) -> None:
        # The review record must never carry resume/cover-letter body text.
        _scaffold(self.root, draft_id="p-apply-1", lead_id="p")
        p = self._review()[0]
        forbidden = {"resume_text", "cover_letter", "cover_letter_text", "body",
                     "prose", "raw_description", "claim_text"}
        self.assertEqual(forbidden & set(p.keys()), set())


if __name__ == "__main__":
    unittest.main()
