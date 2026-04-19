"""Phase 7 tests for ``application.apply_batch`` + batch CLIs.

Exercises: happy path, concurrent-batch rejection, daily-cap enforcement,
pipelining proof (prepare_application(N+1) overlaps lead N), dry-run
safety, empty-selection error, summary + report rendering.

Wall-clock pacing is tamed via ``sleep_fn=lambda _: None`` and an explicit
seeded ``random.Random`` — tests run in milliseconds and still exercise
the distribution sampler.
"""

from __future__ import annotations

import random
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.application import (
    PlanError,
    _select_leads,
    apply_batch,
    batch_cancel,
    batch_status,
    list_batches,
)
from job_hunt.utils import file_lock, read_json, write_json


PROFILE = {
    "contact": {"emails": ["x@x.com"], "phones": [], "links": ["https://www.linkedin.com/in/x/"]},
    "documents": [{"document_id": "d1", "document_type": "resume", "title": "R", "source_excerpt": ""}],
    "skills": [{"name": "python", "source_document_ids": ["d1"]}],
    "experience_highlights": [{"summary": "Shipped 2021 2026", "source_document_ids": ["d1"]}],
    "question_bank": [],
    "preferences": {
        "target_titles": ["Senior SWE"],
        "preferred_locations": ["Remote"],
        "remote_preference": "remote",
        "excluded_keywords": [],
        "work_authorization": "US Citizen",
        "sponsorship_required": False,
    },
}

POLICY_NO_SLEEP = {
    "approval_required_before_submit": True,
    "approval_required_before_account_creation": True,
    "apply_policy": {
        "auto_submit_tiers": [],
        "stale_attempt_threshold_minutes": 45,
        "inter_application_delay_seconds": [0, 0],
        "inter_application_pacing_distribution": "uniform",
        "inter_application_coffee_break_every_n": 0,
        "inter_application_daily_cap": 50,
    },
}


def _seed_bank(data_root: Path) -> None:
    write_json(data_root / "answer-bank.json", {
        "schema_version": 1,
        "entries": [
            {"entry_id": f"e{i}", "canonical_question": q, "observed_variants": [],
             "answer": a, "answer_format": "text", "source": "curated", "reviewed": True,
             "deprecated": False, "created_at": "2026-04-17T00:00:00Z",
             "reviewed_at": "2026-04-17T00:00:00Z", "time_sensitive": False,
             "valid_until": None, "notes": None}
            for i, (q, a) in enumerate([
                ("are you legally authorized to work in the united states", "Yes"),
                ("will you now or in the future require sponsorship for employment visa status", "No"),
                ("are you willing to work remotely", "Yes"),
                ("when can you start", "Two weeks"),
                ("what is your minimum salary expectation", "$140k"),
                ("linkedin url", "https://linkedin.com/in/x/"),
                ("why are you interested in this role", "Because."),
            ])
        ],
    })


def _seed_leads(leads_dir: Path, count: int, *, source: str = "indeed") -> None:
    leads_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        lead = {
            "lead_id": f"indeed-lead-{i:03d}",
            "company": f"Co{i}",
            "title": "Senior Platform Engineer",
            "location": "Remote",
            "raw_description": "Python + AWS role.",
            "canonical_url": f"https://www.indeed.com/viewjob?jk={i:016x}",
            "normalized_requirements": {"keywords": ["python"], "required": []},
            "fit_assessment": {"matched_skills": ["python"], "fit_score": 80 + i},
            "status": "scored",
            "source": source,
        }
        write_json(leads_dir / f"{lead['lead_id']}.json", lead)


class BatchHappyPathTest(unittest.TestCase):
    def test_happy_path_top_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 3)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = apply_batch(
                    top=3, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    rng=random.Random(42),
                    enable_heartbeat_thread=False,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["results"]), 3)
            # Summary + report on disk.
            self.assertTrue(Path(result["summary_path"]).exists())
            self.assertTrue(Path(result["report_path"]).exists())

    def test_dry_run_records_dry_run_only_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 2)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = apply_batch(
                    top=2, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    dry_run=True,
                    sleep_fn=lambda _: None,
                    rng=random.Random(7),
                    enable_heartbeat_thread=False,
                )
            for r in result["results"]:
                self.assertEqual(r["final_status"], "dry_run_only")
            # No real submitted state anywhere — lifecycle remains drafted.
            apps = data_root / "applications"
            for draft_dir in apps.iterdir():
                if draft_dir.name == "batches":
                    continue
                status = read_json(draft_dir / "status.json")
                self.assertIn(status["lifecycle_state"], ("drafted", "applying"))


class ConcurrentBatchRejectionTest(unittest.TestCase):
    def test_second_apply_batch_rejected_while_lock_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 1)
            lock_path = data_root / "applications" / "batches" / ".lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.touch()

            with file_lock(lock_path, check_mtime=False):
                with self.assertRaises(PlanError) as ctx:
                    apply_batch(
                        top=1, runtime_policy=POLICY_NO_SLEEP,
                        candidate_profile=PROFILE,
                        data_root=data_root,
                        leads_dir=data_root / "leads",
                        sleep_fn=lambda _: None,
                        enable_heartbeat_thread=False,
                    )
                self.assertEqual(ctx.exception.error_code, "batch_already_running")


class DailyCapTest(unittest.TestCase):
    def test_cap_blocks_batch_when_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 1)

            # Pre-seed a submitted_provisional attempt for today to trip the cap.
            applications = data_root / "applications" / "fake-draft" / "attempts"
            applications.mkdir(parents=True)
            from datetime import UTC, datetime
            today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            write_json(applications / "20260417T120000Z-aaaaaaaa.json", {
                "schema_version": 1,
                "draft_id": "fake-draft",
                "batch_id": "old-batch-1",
                "attempt_filename": "20260417T120000Z-aaaaaaaa.json",
                "status": "submitted_provisional",
                "checkpoint": "confirmation_captured",
                "recorded_at": today,
            })

            cap_policy = {
                **POLICY_NO_SLEEP,
                "apply_policy": {**POLICY_NO_SLEEP["apply_policy"], "inter_application_daily_cap": 1},
            }
            with self.assertRaises(PlanError) as ctx:
                apply_batch(
                    top=1, runtime_policy=cap_policy,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    enable_heartbeat_thread=False,
                )
            self.assertEqual(ctx.exception.error_code, "daily_cap_reached")


class EmptySelectionTest(unittest.TestCase):
    def test_no_leads_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            (data_root / "leads").mkdir(parents=True)
            with self.assertRaises(PlanError) as ctx:
                apply_batch(
                    top=5, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    enable_heartbeat_thread=False,
                )
            self.assertEqual(ctx.exception.error_code, "no_scored_leads")


class PipeliningTest(unittest.TestCase):
    """Assert that prepare_application(N+1) timestamp overlaps sleep(N).

    The plan calls this out as a hard requirement for the ≤35min success
    metric. We instrument prepare_application via a patched timestamp log
    and assert the pipelined calls are observed.
    """

    def test_background_prep_happens_during_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 3)

            sleep_events: list[tuple[float, str]] = []

            def instrumented_sleep(delay: float) -> None:
                sleep_events.append((time.monotonic(), "sleep_start"))
                # Give the background prep thread a slice to run.
                time.sleep(0.05)
                sleep_events.append((time.monotonic(), "sleep_end"))

            # Wrap prepare_application so we can see the timeline.
            from job_hunt import application as app_mod

            original_prep = app_mod.prepare_application
            prep_events: list[tuple[float, str]] = []
            prep_lock = threading.Lock()

            def counting_prep(*a, **kw):
                with prep_lock:
                    prep_events.append((time.monotonic(), "prep_start"))
                result = original_prep(*a, **kw)
                with prep_lock:
                    prep_events.append((time.monotonic(), "prep_end"))
                return result

            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])), \
                 patch("job_hunt.application.prepare_application", side_effect=counting_prep):
                apply_batch(
                    top=3, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=instrumented_sleep,
                    rng=random.Random(1),
                    enable_heartbeat_thread=False,
                )
            # Pipelining test: any prepare_application INTERVAL must
            # overlap a sleep INTERVAL. (The bg prep can start slightly
            # before the sleep — during the apply_posting step — and end
            # during the sleep; any non-empty intersection counts.)
            sleep_windows = [
                (sleep_events[i][0], sleep_events[i + 1][0])
                for i in range(0, len(sleep_events), 2)
            ]
            prep_intervals: list[tuple[float, float]] = []
            pending_start: float | None = None
            for t, kind in prep_events:
                if kind == "prep_start":
                    pending_start = t
                elif kind == "prep_end" and pending_start is not None:
                    prep_intervals.append((pending_start, t))
                    pending_start = None

            def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
                return max(a[0], b[0]) < min(a[1], b[1])

            overlap_count = sum(
                1 for p in prep_intervals
                for s in sleep_windows
                if _overlaps(p, s)
            )
            self.assertGreaterEqual(
                overlap_count, 1,
                f"pipelining did not overlap any sleep. prep={prep_intervals} sleep={sleep_windows}",
            )


class BatchListAndStatusTest(unittest.TestCase):
    def test_list_and_status_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 1)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = apply_batch(
                    top=1, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    enable_heartbeat_thread=False,
                )
            batches = list_batches(data_root=data_root)
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["batch_id"], result["batch_id"])

            payload = batch_status(result["batch_id"], data_root=data_root)
            self.assertIn("summary", payload)


class BatchCancelTest(unittest.TestCase):
    def test_cancel_writes_sentinel_and_marks_aborted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 1)
            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = apply_batch(
                    top=1, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    enable_heartbeat_thread=False,
                )
            # Rewrite summary to simulate an in-flight batch so cancel flips it.
            summary_path = Path(result["summary_path"])
            s = read_json(summary_path)
            s["status"] = "running"
            write_json(summary_path, s)
            batch_cancel(result["batch_id"], data_root=data_root)
            updated = read_json(summary_path)
            self.assertEqual(updated["status"], "aborted")
            batch_dir = data_root / "applications" / "batches" / result["batch_id"]
            self.assertTrue((batch_dir / "CANCEL").exists())


class AutoSubmitOverrideRejectedTest(unittest.TestCase):
    def test_batch_rejects_auto_submit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 1)
            bad_policy = {
                **POLICY_NO_SLEEP,
                "apply_policy": {**POLICY_NO_SLEEP["apply_policy"], "auto_submit_tiers": ["tier_1"]},
            }
            with self.assertRaises(PlanError) as ctx:
                apply_batch(
                    top=1, runtime_policy=bad_policy,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    enable_heartbeat_thread=False,
                )
            self.assertEqual(ctx.exception.error_code, "policy_loosen_attempt")


class DraftAlreadyExistsSkipTest(unittest.TestCase):
    """When a re-discovered lead's draft already exists (because it was
    previously submitted), ``apply_batch`` must skip it and keep processing
    the rest of the batch instead of aborting.
    """

    def test_batch_continues_past_draft_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_bank(data_root)
            _seed_leads(data_root / "leads", 3)

            # Pre-create the draft dir for lead 0 so prepare_application
            # raises PlanError(draft_already_exists). We do NOT create a
            # plan.json so the preventive _select_leads filter doesn't
            # catch it — this exercises the in-loop safety net.
            from job_hunt.application import _draft_id_for_lead
            draft_id = _draft_id_for_lead("indeed-lead-002")  # top-scored
            stale_dir = data_root / "applications" / draft_id
            stale_dir.mkdir(parents=True)

            with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
                result = apply_batch(
                    top=3, runtime_policy=POLICY_NO_SLEEP,
                    candidate_profile=PROFILE,
                    data_root=data_root,
                    leads_dir=data_root / "leads",
                    sleep_fn=lambda _: None,
                    rng=random.Random(42),
                    enable_heartbeat_thread=False,
                )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["results"]), 3)
            statuses = [r["final_status"] for r in result["results"]]
            self.assertIn("skipped_already_prepared", statuses)
            # The remaining two leads still got prepared.
            self.assertEqual(
                sum(1 for s in statuses if s == "prepared"), 2,
                f"expected 2 prepared leads after skip, got {statuses}",
            )
            # Skipped entry carries the diagnostic error code.
            skipped = [r for r in result["results"] if r["final_status"] == "skipped_already_prepared"]
            self.assertEqual(skipped[0]["error_code"], "draft_already_exists")


class SelectLeadsSkipsSubmittedUrlsTest(unittest.TestCase):
    """``_select_leads`` must filter out leads whose canonical/application
    URL matches an already-submitted draft's ``correlation_keys.posting_url``.
    """

    def test_skips_lead_whose_url_matches_submitted_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            leads_dir = data_root / "leads"
            _seed_leads(leads_dir, 2)

            # lead-000 URL matches a submitted draft on disk.
            lead0 = read_json(leads_dir / "indeed-lead-000.json")
            submitted_url = lead0["canonical_url"]

            submitted_draft = data_root / "applications" / "submitted-draft"
            (submitted_draft / "attempts").mkdir(parents=True)
            write_json(submitted_draft / "plan.json", {
                "schema_version": 1,
                "correlation_keys": {"posting_url": submitted_url},
            })
            write_json(submitted_draft / "status.json", {
                "schema_version": 1,
                "lifecycle_state": "submitted",
                "attempts": [{"status": "submitted_provisional"}],
            })

            selected = _select_leads(
                leads_dir, top=10, source="indeed", score_floor=None,
                data_root=data_root,
            )
            selected_ids = {l["lead_id"] for l in selected}
            self.assertNotIn("indeed-lead-000", selected_ids)
            self.assertIn("indeed-lead-001", selected_ids)

    def test_does_not_skip_when_only_drafted_no_submission(self) -> None:
        # A draft that exists but has no submitted attempt should NOT
        # filter the lead — the batch can legitimately re-prepare it
        # with --force or after a reconcile. (This test documents the
        # boundary of the filter's scope.)
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            leads_dir = data_root / "leads"
            _seed_leads(leads_dir, 1)
            lead0 = read_json(leads_dir / "indeed-lead-000.json")

            draft = data_root / "applications" / "drafted-only"
            (draft / "attempts").mkdir(parents=True)
            write_json(draft / "plan.json", {
                "schema_version": 1,
                "correlation_keys": {"posting_url": lead0["canonical_url"]},
            })
            write_json(draft / "status.json", {
                "schema_version": 1,
                "lifecycle_state": "drafted",
                "attempts": [],
            })

            selected = _select_leads(
                leads_dir, top=10, source="indeed", score_floor=None,
                data_root=data_root,
            )
            self.assertEqual({l["lead_id"] for l in selected}, {"indeed-lead-000"})


if __name__ == "__main__":
    unittest.main()
