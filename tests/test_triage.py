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


from job_hunt.triage import (  # noqa: E402
    bridge_recruiter,
    build_correlation_index,
    classify_recruiter_email,
    correlate_recruiter,
    dkim_pass_domain,
    redact_email,
    registrable_domain,
)


def _rmail(subject: str, body: str, *, auth: str = "", mid: str = "r1") -> ParsedEmail:
    return ParsedEmail(
        sender="recruiting@acme.com", message_id=mid, subject=subject,
        body=body, authentication_results=auth, event_type="submitted",
        posting_url=None, indeed_jk=None,
    )


class ClassifierTest(unittest.TestCase):
    def test_truth_table(self) -> None:
        cases = {
            "We're excited to offer you the role": "offer",
            "Please complete the take-home coding challenge": "assessment_request",
            "Let's schedule a quick chat with the recruiter — phone screen": "phone_screen",
            "Invitation: onsite interview next round": "interview",
            "We regret to inform you we are not moving forward": "rejection",
            "Your account statement is ready": "unknown",
        }
        for body, expected in cases.items():
            self.assertEqual(classify_recruiter_email(_rmail("", body)).label, expected)

    def test_offer_beats_rejection_in_mixed_email(self) -> None:
        c = classify_recruiter_email(_rmail("", "we regret... but your offer letter attached"))
        self.assertEqual(c.label, "offer")

    def test_matched_rule_recorded_for_audit(self) -> None:
        c = classify_recruiter_email(_rmail("", "regret to inform"))
        self.assertEqual(c.matched_rule, "regret to inform")


class RedactionTest(unittest.TestCase):
    def test_strips_pii_and_html_in_subject_and_body(self) -> None:
        p = _rmail("ping me@x.com", "<p>call +1 (415) 555-1234</p> me@x.com")
        r = redact_email(p)
        for blob in (r.subject, r.body):
            self.assertNotIn("me@x.com", blob)
            self.assertNotIn("555-1234", blob)
        self.assertNotIn("<p>", r.body)
        # frozen dataclass not mutated in place
        self.assertEqual(p.body, "<p>call +1 (415) 555-1234</p> me@x.com")

    def test_body_size_bounded(self) -> None:
        r = redact_email(_rmail("s", "x" * (300 * 1024)))
        self.assertLessEqual(len(r.body), 256 * 1024)


class RegistrableDomainTest(unittest.TestCase):
    def test_lookalikes_do_not_reduce_to_company(self) -> None:
        self.assertEqual(registrable_domain("jobs.stripe.com"), "stripe.com")
        self.assertNotEqual(registrable_domain("stripe-careers.com"), "stripe.com")
        self.assertEqual(registrable_domain("stripe.com.evil.net"), "evil.net")
        self.assertEqual(registrable_domain("careers.foo.co.uk"), "foo.co.uk")

    def test_dkim_domain_only_on_pass(self) -> None:
        self.assertEqual(
            dkim_pass_domain("spf=pass; dkim=pass header.d=acme.com"), "acme.com")
        self.assertIsNone(dkim_pass_domain("dkim=fail header.d=acme.com"))
        self.assertIsNone(dkim_pass_domain(""))


class CorrelateRecruiterTest(unittest.TestCase):
    def _index(self, tmp: Path):
        (tmp / "companies").mkdir(parents=True)
        write_json(tmp / "companies" / "c1.json", {
            "company_id": "c1", "company_name": "Acme",
            "source_urls": ["https://www.acme.com/about"],
        })
        (tmp / "leads").mkdir(parents=True)
        write_json(tmp / "leads" / "L1.json", {
            "lead_id": "L1", "company": "Acme Corp", "company_research_id": "c1",
        })
        return build_correlation_index(tmp)

    def test_dkim_domain_match_resolves_single_lead(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            idx = self._index(Path(t))
            e = _rmail("Acme update", "Hi from Acme recruiting",
                       auth="dkim=pass header.d=acme.com")
            r = correlate_recruiter(e, idx)
            self.assertEqual((r.decision, r.lead_id), ("match", "L1"))

    def test_spoofed_from_with_unrelated_dkim_is_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            idx = self._index(Path(t))
            # Attacker controls evil.com, name-drops Acme in the body.
            e = _rmail("Acme: rejection", "regret to inform — Acme",
                       auth="dkim=pass header.d=evil.com")
            self.assertEqual(correlate_recruiter(e, idx).decision, "no_match")

    def test_no_dkim_pass_is_sender_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            idx = self._index(Path(t))
            self.assertEqual(
                correlate_recruiter(_rmail("Acme", "Acme"), idx).decision,
                "sender_unverified",
            )


class BridgeRecruiterTest(unittest.TestCase):
    def test_assessment_request_does_not_change_stage(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p = _status(root, "L1", "phone_screen")
            from job_hunt.triage import RecruiterClass
            r = bridge_recruiter(
                _rmail("", "take-home"), RecruiterClass("assessment_request", "take-home"),
                lead_id="L1", data_root=root,
            )
            self.assertEqual(r.outcome, "noop_backward")
            self.assertEqual(read_json(p)["current_stage"], "phone_screen")

    def test_phone_screen_advances(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _status(root, "L1", "applied")
            from job_hunt.triage import RecruiterClass
            r = bridge_recruiter(
                _rmail("", "phone screen"), RecruiterClass("phone_screen", "phone screen"),
                lead_id="L1", data_root=root,
            )
            self.assertEqual((r.outcome, r.to_stage), ("advanced", "phone_screen"))


class GhostScanTest(unittest.TestCase):
    def _stale_status(self, root: Path, lead_id: str, stage: str, ts: str):
        d = root / "applications"
        d.mkdir(parents=True, exist_ok=True)
        write_json(d / f"{lead_id}-status.json", {
            "lead_id": lead_id, "current_stage": stage,
            "transitions": [{"from_stage": "applied", "to_stage": stage,
                             "timestamp": ts}],
            "created_at": ts, "updated_at": ts,
        })

    def test_stale_lead_ghosted_fresh_skipped(self) -> None:
        from job_hunt.triage import scan_ghost_timeouts
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._stale_status(root, "OLD", "phone_screen", "2026-01-01T00:00:00+00:00")
            self._stale_status(root, "NEW", "phone_screen", "2026-05-17T00:00:00+00:00")
            res = scan_ghost_timeouts(data_root=root, days=21)
            ghosted = {r["lead_id"]: r["outcome"] for r in res}
            self.assertEqual(ghosted.get("OLD"), "advanced")
            self.assertNotIn("NEW", ghosted)
            self.assertEqual(
                read_json(root / "applications" / "OLD-status.json")["current_stage"],
                "ghosted",
            )

    def test_dry_run_writes_nothing(self) -> None:
        from job_hunt.triage import scan_ghost_timeouts
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._stale_status(root, "OLD", "onsite", "2026-01-01T00:00:00+00:00")
            res = scan_ghost_timeouts(data_root=root, days=21, dry_run=True)
            self.assertEqual(res[0]["outcome"], "would_ghost")
            self.assertEqual(
                read_json(root / "applications" / "OLD-status.json")["current_stage"],
                "onsite",
            )

    def test_terminal_and_already_ghosted_skipped(self) -> None:
        from job_hunt.triage import scan_ghost_timeouts
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._stale_status(root, "REJ", "rejected", "2026-01-01T00:00:00+00:00")
            self._stale_status(root, "GH", "ghosted", "2026-01-01T00:00:00+00:00")
            self.assertEqual(scan_ghost_timeouts(data_root=root, days=21), [])


class CheckIntegrityUnbridgedTest(unittest.TestCase):
    def test_model_a_event_without_model_b_is_flagged(self) -> None:
        from job_hunt.tracking import check_integrity
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = root / "applications" / "draft1"
            d.mkdir(parents=True)
            write_json(d / "plan.json", {"draft_id": "draft1", "lead_id": "L1"})
            write_json(d / "status.json", {
                "draft_id": "draft1", "lifecycle_state": "rejected",
                "events": [{"event_id": "abc", "type": "rejected"}],
            })
            # No {L1}-status.json bridge → divergence.
            report = check_integrity(root)
            flagged = report["unbridged_confirmations"]
            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["event_id"], "abc")
            self.assertTrue(report["summary"]["has_issues"])


class TriageInboxAntiSpoofTest(unittest.TestCase):
    def test_non_allowlisted_outcome_quarantined_not_applied(self) -> None:
        from job_hunt.triage import triage_inbox
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "companies").mkdir(parents=True)
            write_json(root / "companies" / "c1.json", {
                "company_id": "c1", "company_name": "Acme",
                "source_urls": ["https://acme.com"],
            })
            (root / "leads").mkdir(parents=True)
            write_json(root / "leads" / "L1.json", {
                "lead_id": "L1", "company": "Acme", "company_research_id": "c1",
            })
            _status(root, "L1", "applied")
            # DKIM-domain-matched recruiter REJECTION from a non-allowlisted
            # sender → must quarantine for human review, never auto-reject.
            e = ParsedEmail(
                sender="recruiter@acme.com", message_id="x1",
                subject="Acme update", body="we regret to inform you — Acme",
                authentication_results="dkim=pass header.d=acme.com",
                event_type="submitted", posting_url=None, indeed_jk=None,
            )
            roll = triage_inbox([e], data_root=root)
            self.assertEqual(roll["quarantined"], 1)
            self.assertEqual(
                read_json(root / "applications" / "L1-status.json")["current_stage"],
                "applied",
            )


class TrustInvariantTest(unittest.TestCase):
    def test_triage_module_has_no_llm_or_runtime_config(self) -> None:
        src = (SRC / "job_hunt" / "triage.py").read_text(encoding="utf-8")
        for forbidden in ("import openai", "import anthropic",
                          "from openai", "from anthropic",
                          "os.environ", "argparse"):
            self.assertNotIn(forbidden, src,
                             f"triage.py must stay pure/deterministic: {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
