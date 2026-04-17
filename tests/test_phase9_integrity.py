"""Phase 9 tests — check-integrity Batch-4 extensions + prune/cleanup CLIs.

Doesn't depend on the rest of the pipeline — seeds tiny synthetic
draft directories, asserts the new check_integrity report fields are
populated correctly, and round-trips prune_applications +
cleanup_orphans.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.application import cleanup_orphans, prune_applications
from job_hunt.tracking import check_integrity
from job_hunt.utils import write_json


def _seed_minimal_draft(
    data_root: Path,
    *,
    draft_id: str,
    prepared_at: str,
    in_progress_recorded_at: str | None = None,
) -> Path:
    draft_dir = data_root / "applications" / draft_id
    (draft_dir / "attempts").mkdir(parents=True)
    (draft_dir / "checkpoints").mkdir(parents=True)
    write_json(draft_dir / "plan.json", {
        "schema_version": 1,
        "draft_id": draft_id,
        "lead_id": draft_id,
        "surface": "indeed_easy_apply",
        "playbook_path": "playbooks/application/indeed-easy-apply.md",
        "correlation_keys": {"posting_url": "https://example.com", "company": "X", "title": "T"},
        "profile_snapshot": {"snapshot_version": 1, "snapshot_at": prepared_at},
        "untrusted_fetched_content": {"job_description": "", "nonce": "0123456789abcdef"},
        "fields": [],
        "tier": "tier_2",
        "tier_rationale": "test",
        "prepared_at": prepared_at,
    })
    write_json(draft_dir / "status.json", {
        "lead_id": draft_id,
        "draft_id": draft_id,
        "current_stage": "not_applied",
        "lifecycle_state": "drafted",
        "transitions": [],
        "attempts": [],
        "events": [],
        "created_at": prepared_at,
        "updated_at": prepared_at,
    })
    if in_progress_recorded_at:
        attempt_path = draft_dir / "attempts" / "20260101T000000Z-aaaaaaaa.json"
        write_json(attempt_path, {
            "schema_version": 1,
            "draft_id": draft_id,
            "batch_id": "adhoc-test-deadbeef",
            "attempt_filename": attempt_path.name,
            "status": "in_progress",
            "checkpoint": "preflight_done",
            "recorded_at": in_progress_recorded_at,
        })
    return draft_dir


class CheckIntegrityBatch4Test(unittest.TestCase):
    def test_stale_in_progress_attempts_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            stale_iso = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            fresh_iso = datetime.now(UTC).isoformat()
            _seed_minimal_draft(
                data_root, draft_id="stale-draft",
                prepared_at=fresh_iso, in_progress_recorded_at=stale_iso,
            )
            _seed_minimal_draft(
                data_root, draft_id="fresh-draft",
                prepared_at=fresh_iso, in_progress_recorded_at=fresh_iso,
            )
            report = check_integrity(data_root)
            stale = [s["draft_id"] for s in report["stale_in_progress_attempts"]]
            self.assertIn("stale-draft", stale)
            self.assertNotIn("fresh-draft", stale)

    def test_orphan_checkpoints_dirs_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            orphan = data_root / "applications" / "orphan-draft" / "checkpoints"
            orphan.mkdir(parents=True)
            (orphan / "ghost.png").write_bytes(b"\x89PNG")
            report = check_integrity(data_root)
            paths = [o["path"] for o in report["orphan_checkpoints_dirs"]]
            self.assertTrue(any("orphan-draft" in p for p in paths))

    def test_quarantined_confirmations_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            (data_root / "applications" / "_suspicious").mkdir(parents=True)
            write_json(
                data_root / "applications" / "_suspicious" / "msg.json",
                {"reason": "sender_allowlist_mismatch"},
            )
            report = check_integrity(data_root)
            self.assertEqual(len(report["quarantined_confirmations"]), 1)

    def test_stale_inferred_bank_entries_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            data_root.mkdir(parents=True)
            old_iso = (datetime.now(UTC) - timedelta(days=60)).isoformat()
            write_json(data_root / "answer-bank.json", {
                "schema_version": 1,
                "entries": [
                    {
                        "entry_id": "stale_inferred",
                        "canonical_question": "x",
                        "answer": "x",
                        "answer_format": "text",
                        "source": "inferred",
                        "reviewed": False,
                        "deprecated": False,
                        "created_at": old_iso,
                        "observed_variants": [],
                    },
                    {
                        "entry_id": "fresh_curated",
                        "canonical_question": "y",
                        "answer": "y",
                        "answer_format": "text",
                        "source": "curated",
                        "reviewed": True,
                        "deprecated": False,
                        "created_at": datetime.now(UTC).isoformat(),
                        "observed_variants": [],
                    },
                ],
            })
            report = check_integrity(data_root)
            ids = [e["entry_id"] for e in report["stale_inferred_bank_entries"]]
            self.assertEqual(ids, ["stale_inferred"])

    def test_retention_overdue_drafts_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            old_iso = (datetime.now(UTC) - timedelta(days=400)).isoformat()
            _seed_minimal_draft(
                data_root, draft_id="overdue", prepared_at=old_iso,
            )
            report = check_integrity(data_root)
            ids = [d["draft_id"] for d in report["retention_overdue_drafts"]]
            self.assertIn("overdue", ids)


class PruneApplicationsTest(unittest.TestCase):
    def test_dry_run_lists_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            old_iso = (datetime.now(UTC) - timedelta(days=400)).isoformat()
            draft_dir = _seed_minimal_draft(data_root, draft_id="overdue", prepared_at=old_iso)
            result = prune_applications(
                older_than_days=365, dry_run=True, data_root=data_root,
            )
            self.assertTrue(any("overdue" in p for p in result["would_remove"]))
            self.assertEqual(result["removed"], [])
            self.assertTrue(draft_dir.exists())

    def test_real_run_deletes_overdue_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            old_iso = (datetime.now(UTC) - timedelta(days=400)).isoformat()
            draft_dir = _seed_minimal_draft(data_root, draft_id="overdue", prepared_at=old_iso)
            recent_iso = datetime.now(UTC).isoformat()
            keep_dir = _seed_minimal_draft(data_root, draft_id="keep", prepared_at=recent_iso)
            result = prune_applications(
                older_than_days=365, dry_run=False, data_root=data_root,
            )
            self.assertTrue(any("overdue" in p for p in result["removed"]))
            self.assertFalse(draft_dir.exists())
            self.assertTrue(keep_dir.exists())


class CleanupOrphansTest(unittest.TestCase):
    def test_requires_confirm_to_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            orphan = data_root / "applications" / "orphan-draft" / "checkpoints"
            orphan.mkdir(parents=True)
            preview = cleanup_orphans(confirm=False, data_root=data_root)
            self.assertIn("would_remove", preview)
            self.assertTrue(orphan.exists())
            cleanup_orphans(confirm=True, data_root=data_root)
            self.assertFalse(orphan.exists())


if __name__ == "__main__":
    unittest.main()
