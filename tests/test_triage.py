from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.analytics import (  # noqa: E402
    STAGE_SEQUENCE,
    _applied_date,
    _stage_conversions,
    _terminal_outcome,
)
from job_hunt.confirmation import ParsedEmail  # noqa: E402
from job_hunt.triage import (  # noqa: E402
    STAGE_LADDER,
    BridgeResult,
    bridge_event,
    event_id_for,
)
from job_hunt.tracking import update_application_status  # noqa: E402
from job_hunt.utils import file_lock, read_json, write_json  # noqa: E402


def _email(event_type: str, *, mid: str = "m1") -> ParsedEmail:
    return ParsedEmail(
        sender="myindeed@indeed.com",
        message_id=mid,
        subject="subject",
        body="body",
        authentication_results="dkim=pass",
        event_type=event_type,
        posting_url=None,
        indeed_jk=None,
    )


def _status(root: Path, lead_id: str, stage: str, transitions=None) -> Path:
    d = root / "applications"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{lead_id}-status.json"
    write_json(p, {
        "lead_id": lead_id,
        "current_stage": stage,
        "transitions": transitions or [],
        "generated_content_ids": [],
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
    })
    return p


class StageLadderConsistencyTest(unittest.TestCase):
    def test_ladder_matches_analytics_sequence(self) -> None:
        # The ladder must rank STAGE_SEQUENCE in the same order — one source
        # of truth across triage/analytics/confirmation.
        ranks = [STAGE_LADDER[s] for s in STAGE_SEQUENCE]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(STAGE_LADDER["not_applied"], 0)
        self.assertLess(STAGE_LADDER["applied"], STAGE_LADDER["offer"])


class BridgeEventTest(unittest.TestCase):
    def test_advances_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _status(root, "L1", "applied")
            r = bridge_event(_email("rejected"), lead_id="L1", data_root=root)
            self.assertEqual(r.outcome, "advanced")
            self.assertEqual(r.to_stage, "rejected")
            s = read_json(p)
            self.assertEqual(s["current_stage"], "rejected")
            self.assertEqual(s["transitions"][-1]["event_id"], r.event_id)

    def test_interview_maps_to_onsite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _status(root, "L1", "applied")
            r = bridge_event(_email("interview"), lead_id="L1", data_root=root)
            self.assertEqual((r.outcome, r.to_stage), ("advanced", "onsite"))

    def test_idempotent_across_repoll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _status(root, "L1", "applied")
            e = _email("offer", mid="same")
            r1 = bridge_event(e, lead_id="L1", data_root=root)
            r2 = bridge_event(e, lead_id="L1", data_root=root)
            self.assertEqual(r1.outcome, "advanced")
            self.assertEqual(r2.outcome, "noop_duplicate")
            self.assertEqual(len(read_json(p)["transitions"]), 1)

    def test_inferred_skip_flagged_and_excluded_from_funnel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _status(root, "L1", "applied")
            r = bridge_event(_email("offer"), lead_id="L1", data_root=root)
            self.assertEqual(r.outcome, "advanced")
            self.assertTrue(r.inferred_skip)  # applied → offer is a >1 jump
            txn = read_json(p)["transitions"][-1]
            self.assertTrue(txn["inferred_skip"])
            # analytics must not count the skipped 'offer' as a reached stage
            row = {"current_stage": "offer", "transitions": read_json(p)["transitions"]}
            conv = _stage_conversions([row])
            self.assertEqual(conv["onsite_to_offer"]["to"], 0)

    def test_backward_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _status(root, "L1", "onsite")
            r = bridge_event(_email("submitted"), lead_id="L1", data_root=root)
            self.assertEqual(r.outcome, "noop_backward")
            self.assertEqual(read_json(p)["transitions"], [])

    def test_terminal_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _status(root, "L1", "rejected")
            r = bridge_event(_email("offer"), lead_id="L1", data_root=root)
            self.assertEqual(r.outcome, "noop_terminal")
            self.assertEqual(read_json(p)["transitions"], [])

    def test_creates_missing_status_under_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "applications").mkdir(parents=True)
            r = bridge_event(_email("confirmed"), lead_id="NEW", data_root=root)
            self.assertEqual(r.outcome, "advanced")
            s = read_json(root / "applications" / "NEW-status.json")
            self.assertEqual(s["current_stage"], "applied")
            self.assertEqual(s["transitions"][0]["from_stage"], "not_applied")

    def test_contention_skips_not_corrupts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = _status(root, "L1", "applied")
            with file_lock(p, check_mtime=False):
                r = bridge_event(_email("rejected"), lead_id="L1", data_root=root)
            self.assertEqual(r.outcome, "skipped_contention")
            self.assertEqual(read_json(p)["transitions"], [])  # untouched


class TrackingBackCompatTest(unittest.TestCase):
    """The manual update-status path must be byte-shape unchanged (no
    event_id/inferred_skip keys) and still enforce its guards under the
    newly-added lock."""

    def test_manual_transition_shape_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(Path(tmp), "L1", "applied")
            s = update_application_status(p, "phone_screen", note="call booked")
            txn = s["transitions"][-1]
            self.assertEqual(set(txn), {"from_stage", "to_stage", "timestamp", "note"})

    def test_terminal_guard_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(Path(tmp), "L1", "rejected")
            with self.assertRaises(ValueError):
                update_application_status(p, "onsite")


class AnalyticsHistoryTest(unittest.TestCase):
    def test_terminal_outcome_recovers_ghost_reactivation(self) -> None:
        # ghosted → onsite: current_stage is onsite, but the ghost outcome
        # must not be lost to calibrate-scoring.
        row = {
            "current_stage": "onsite",
            "transitions": [
                {"to_stage": "applied"},
                {"to_stage": "ghosted"},
                {"to_stage": "onsite"},
            ],
        }
        self.assertEqual(_terminal_outcome(row), "ghosted")

    def test_terminal_outcome_unchanged_for_normal_case(self) -> None:
        row = {"current_stage": "rejected",
               "transitions": [{"to_stage": "applied"}, {"to_stage": "rejected"}]}
        self.assertEqual(_terminal_outcome(row), "rejected")

    def test_applied_date_skips_inferred_skip(self) -> None:
        self.assertIsNone(_applied_date([
            {"to_stage": "applied", "timestamp": "t", "inferred_skip": True},
        ]))
        self.assertEqual(_applied_date([
            {"to_stage": "applied", "timestamp": "t2"},
        ]), "t2")


if __name__ == "__main__":
    unittest.main()
