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

from job_hunt import packet_checklist as pc

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

# Sentinels that represent PRIVATE prose / claim text. They are written into the
# descriptor prose fields, the markdown asset files, and the claims bank, then
# asserted ABSENT from the rendered checklist (the module reads metadata only).
PRIVATE_RESUME_PROSE = "SECRET_RESUME_BODY_led_a_team_of_ninjas"
PRIVATE_COVER_PROSE = "SECRET_COVER_BODY_dear_hiring_manager_xyz"
PRIVATE_CLAIM_TEXT = "SECRET_CLAIM_increased_revenue_by_42_percent"


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _scaffold(
    root: Path,
    *,
    draft_id: str = "acme-backend-engineer-apply-0001",
    lead_id: str = "acme-backend-engineer-0001",
    company: str = "Acme",
    title: str = "Backend Engineer",
    posting_url: str = "https://boards.greenhouse.io/acme/jobs/123",
    lane: str = "technical_depth",
    pdf: str = "ready",  # "ready" | "none"
    claim_ids: tuple[str, ...] = ("c-approved-1", "c-approved-2"),
    gen_warnings: tuple[str, ...] = (),
    location: str = "Toronto, CAN-Remote",
) -> Path:
    """Create one synthetic packet + generated descriptors. Returns its dir."""
    d = root / "applications" / draft_id
    resume_cid = f"{lead_id}-{lane}-20260620T000000"
    cover_cid = f"{lead_id}-cover-letter-20260620T000000"
    pdf_ref_status = "ready" if pdf == "ready" else "not_attempted"
    _write(d / "status.json", {
        "draft_id": draft_id,
        "lead_id": lead_id,
        "lifecycle_state": "drafted",
        "current_stage": "not_applied",
        "requires_human_submit": True,
        "tier": "tier_1",
        "generated_content_ids": [resume_cid, cover_cid],
        "routing_snapshot": {"posting_url": posting_url},
    })
    _write(d / "plan.json", {
        "draft_id": draft_id,
        "lead_id": lead_id,
        "tier": "tier_1",
        "ats_check": {"status": "ok", "errors": [], "warnings": ["w0"]},
        "coherence_warnings": [],
        "generated_asset_refs": {
            "resume": {"available": True, "pdf_export_status": pdf_ref_status},
            "cover_letter": {"available": True, "pdf_export_status": pdf_ref_status},
        },
    })
    _write(root / "leads" / f"{lead_id}.json", {
        "lead_id": lead_id,
        "company": company,
        "title": title,
        "location": location,
        "application_url": posting_url,
        "fit_assessment": {
            "fit_score": 87,
            "fit_recommendation": "strong_yes",
            "seniority_reason": "seniority-unspecified",
        },
        "ingested_at": "2026-06-20T06:00:00+00:00",
    })
    sources = [f"claim:{c}" for c in claim_ids]
    resume_md = f"data/generated/resumes/{resume_cid}.md"
    cover_md = f"data/generated/cover-letters/{cover_cid}.md"

    def _pdf_fields(md_path: str) -> dict:
        return {"pdf_path": md_path.replace(".md", ".pdf")} if pdf == "ready" else {}

    # Descriptors carry a PROSE field that the checklist must never read.
    _write(root / "generated" / "resumes" / f"{resume_cid}.json", {
        "content_id": resume_cid,
        "variant_style": lane,
        "source_document_ids": sources,
        "output_path": resume_md,
        "rendered_markdown": PRIVATE_RESUME_PROSE,
        **_pdf_fields(resume_md),
    })
    _write(root / "generated" / "cover-letters" / f"{cover_cid}.json", {
        "content_id": cover_cid,
        "lane_id": "platform_internal_tools",
        "source_document_ids": sources,
        "generation_warnings": [{"code": w} for w in gen_warnings],
        "output_path": cover_md,
        "rendered_markdown": PRIVATE_COVER_PROSE,
        **_pdf_fields(cover_md),
    })
    # The actual prose files on disk also hold sentinels.
    (root / "generated" / "resumes" / f"{resume_cid}.md").write_text(
        PRIVATE_RESUME_PROSE, encoding="utf-8")
    (root / "generated" / "cover-letters" / f"{cover_cid}.md").write_text(
        PRIVATE_COVER_PROSE, encoding="utf-8")
    return d


CLAIMS = {
    "schema_version": 1,
    "claims": [
        {"claim_id": "c-approved-1", "review_status": "approved", "text": PRIVATE_CLAIM_TEXT},
        {"claim_id": "c-approved-2", "review_status": "approved", "text": PRIVATE_CLAIM_TEXT},
    ],
}


class PacketChecklistTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data"
        self.claims_path = Path(self._tmp.name) / "claims-bank.json"
        _write(self.claims_path, CLAIMS)
        self.claims_index = pc.load_claims_index(self.claims_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_one(self, **kw) -> Path:
        d = _scaffold(self.root, **kw)
        res = pc.write_checklist(
            d, data_root=self.root, claims_index=self.claims_index, now=NOW)
        return d / pc.CHECKLIST_FILENAME, res

    def test_checklist_file_created(self):
        path, res = self._write_one()
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "MANUAL_SUBMISSION.md")
        self.assertTrue(res["written"])
        self.assertFalse(res["updated"])

    def test_includes_url_and_post_action_commands(self):
        path, _ = self._write_one()
        text = path.read_text(encoding="utf-8")
        self.assertIn("https://boards.greenhouse.io/acme/jobs/123", text)
        # All four post-action mark-packet commands, with the draft id filled in.
        self.assertIn("mark-packet --draft-id acme-backend-engineer-apply-0001 --status manually_submitted", text)
        self.assertIn("--status skipped", text)
        self.assertIn("--status needs_revision", text)
        self.assertIn("--status follow_up_later", text)

    def test_includes_pdf_paths_when_ready(self):
        path, res = self._write_one(pdf="ready")
        text = path.read_text(encoding="utf-8")
        self.assertIn("resumes/acme-backend-engineer-0001-technical_depth-20260620T000000.pdf", text)
        self.assertIn("cover-letters/acme-backend-engineer-0001-cover-letter-20260620T000000.pdf", text)
        self.assertEqual(res["missing_pdf"], [])

    def test_falls_back_to_markdown_when_no_pdf(self):
        path, res = self._write_one(pdf="none")
        text = path.read_text(encoding="utf-8")
        # No PDF path, but the markdown fallback path is present.
        self.assertNotIn(".pdf", text)
        self.assertIn("resumes/acme-backend-engineer-0001-technical_depth-20260620T000000.md", text)
        self.assertIn("cover-letters/acme-backend-engineer-0001-cover-letter-20260620T000000.md", text)
        self.assertEqual(sorted(res["missing_pdf"]), ["cover_letter", "resume"])

    def test_includes_human_submit_safety_language(self):
        path, _ = self._write_one()
        text = path.read_text(encoding="utf-8")
        self.assertIn("requires_human_submit = True", text)
        self.assertIn("Nothing has been submitted", text)
        self.assertIn("review and submit manually", text)
        self.assertIn("Do not rely on generated answers", text)

    def test_no_private_prose_or_claim_text(self):
        path, _ = self._write_one()
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_RESUME_PROSE, text)
        self.assertNotIn(PRIVATE_COVER_PROSE, text)
        self.assertNotIn(PRIVATE_CLAIM_TEXT, text)
        # Claims are summarized as a count only.
        self.assertIn("2/2 approved", text)

    def test_backfill_writes_for_existing_packets(self):
        _scaffold(self.root, draft_id="acme-be-apply-a", lead_id="acme-be-a")
        _scaffold(self.root, draft_id="acme-be-apply-b", lead_id="acme-be-b")
        result = pc.refresh_checklists(
            data_root=self.root, claims_path=self.claims_path, now=NOW)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["written"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["missing_pdf"], 0)
        for draft_id in ("acme-be-apply-a", "acme-be-apply-b"):
            self.assertTrue(
                (self.root / "applications" / draft_id / "MANUAL_SUBMISSION.md").exists())
        # Re-running updates (does not double-count as new writes).
        again = pc.refresh_checklists(
            data_root=self.root, claims_path=self.claims_path, now=NOW)
        self.assertEqual(again["written"], 0)
        self.assertEqual(again["updated"], 2)

    def test_backfill_reports_missing_url(self):
        _scaffold(self.root, draft_id="nourl-apply", lead_id="nourl",
                  posting_url="")
        result = pc.refresh_checklists(
            data_root=self.root, claims_path=self.claims_path, now=NOW)
        self.assertEqual(result["missing_url"], 1)
        self.assertIn("nourl-apply", result["drafts_missing_url"])


if __name__ == "__main__":
    unittest.main()
