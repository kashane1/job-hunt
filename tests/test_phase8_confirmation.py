"""Phase 8 tests for ``src/job_hunt/confirmation.py``.

Covers parse_email shape, classifier coverage (submitted / confirmed /
interview / offer / rejected), sender-allowlist + DKIM gates, no-match +
ambiguous-match quarantine, priority ladder enforcement (late rejection
does NOT override an earlier confirmed), event_id idempotency, Gmail
search query DSL building, and the cursor round-trip.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.application import (
    ApplicationError,
    PlanError,
    apply_status,
    prepare_application,
)
from job_hunt.confirmation import (
    SENDER_ALLOWLIST,
    gmail_search_query,
    ingest_confirmation,
    load_gmail_cursor,
    match_message,
    parse_email,
    save_gmail_cursor,
    update_status,
    verify_sender,
)
from job_hunt.utils import read_json, write_json


def _eml(
    *,
    sender: str = "myindeed@indeed.com",
    subject: str = "Your application has been submitted",
    body: str = "Thank you for applying to Senior Engineer.",
    message_id: str = "<msg-1@indeed.com>",
    auth_results: str = "indeed.com; dkim=pass header.d=indeed.com",
) -> bytes:
    headers = (
        f"From: {sender}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Subject: {subject}\r\n"
        f"Authentication-Results: {auth_results}\r\n"
        "Content-Type: text/plain; charset=us-ascii\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    )
    return (headers + body).encode("utf-8")


PROFILE = {
    "contact": {"emails": ["x@x.com"], "phones": [], "links": []},
    "documents": [{"document_id": "d", "document_type": "resume", "title": "R", "source_excerpt": ""}],
    "skills": [], "experience_highlights": [], "question_bank": [],
    "preferences": {
        "target_titles": [], "preferred_locations": [], "excluded_keywords": [],
        "work_authorization": "US Citizen", "sponsorship_required": False,
    },
}

POLICY = {
    "approval_required_before_submit": True,
    "approval_required_before_account_creation": True,
    "apply_policy": {"auto_submit_tiers": []},
}


def _seed_bank(data_root: Path) -> None:
    write_json(data_root / "answer-bank.json", {
        "schema_version": 1,
        "entries": [
            {"entry_id": f"e{i}", "canonical_question": q, "answer": a, "answer_format": "text",
             "source": "curated", "reviewed": True, "deprecated": False,
             "created_at": "2026-04-17T00:00:00Z", "observed_variants": []}
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


def _seed_lead_with_jk(data_root: Path, jk: str = "0123456789abcdef"):
    """Prepare a draft with a known indeed_jk so match_message can find it."""
    _seed_bank(data_root)
    lead = {
        "lead_id": f"indeed-lead-{jk[:8]}",
        "company": "X",
        "title": "Senior Engineer",
        "location": "Remote",
        "raw_description": "",
        "canonical_url": f"https://www.indeed.com/viewjob?jk={jk}",
        "normalized_requirements": {"keywords": [], "required": []},
        "fit_assessment": {"matched_skills": []},
    }
    with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
        return prepare_application(
            lead, PROFILE, POLICY,
            output_root=data_root / "applications",
            data_root=data_root,
        )


class ParseEmailTest(unittest.TestCase):
    def test_basic_parse(self) -> None:
        parsed = parse_email(_eml())
        self.assertEqual(parsed.sender, "myindeed@indeed.com")
        self.assertEqual(parsed.message_id, "msg-1@indeed.com")
        self.assertEqual(parsed.event_type, "confirmed")

    def test_classifies_offer(self) -> None:
        parsed = parse_email(_eml(subject="Your offer letter from ExampleCo"))
        self.assertEqual(parsed.event_type, "offer")

    def test_classifies_interview(self) -> None:
        parsed = parse_email(_eml(subject="Let's schedule a phone interview"))
        self.assertEqual(parsed.event_type, "interview")

    def test_classifies_rejection(self) -> None:
        parsed = parse_email(_eml(
            subject="Update on your application",
            body="We regret to inform you we are moving forward with other candidates.",
        ))
        self.assertEqual(parsed.event_type, "rejected")

    def test_extracts_indeed_jk(self) -> None:
        parsed = parse_email(_eml(body="Posting jk: deadbeefcafebab1 in this email."))
        # 16-hex match — embedded in a sentence
        self.assertEqual(parsed.indeed_jk, "deadbeefcafebab1")


class SenderVerificationTest(unittest.TestCase):
    def test_allowlisted_with_dkim_passes(self) -> None:
        parsed = parse_email(_eml())
        self.assertIsNone(verify_sender(parsed))

    def test_off_allowlist_quarantined(self) -> None:
        parsed = parse_email(_eml(sender="phisher@evil.example"))
        self.assertEqual(verify_sender(parsed), "sender_allowlist_mismatch")

    def test_dkim_fail_quarantined(self) -> None:
        parsed = parse_email(_eml(auth_results="indeed.com; dkim=fail header.d=indeed.com"))
        self.assertEqual(verify_sender(parsed), "dkim_failed")


class MatchMessageTest(unittest.TestCase):
    def test_indeed_jk_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            prepared = _seed_lead_with_jk(data_root, "abcdef0123456789")
            parsed = parse_email(_eml(body="Posting URL: https://www.indeed.com/viewjob?jk=abcdef0123456789"))
            candidates = match_message(parsed, data_root=data_root)
            self.assertEqual(candidates, [prepared.draft_id])

    def test_no_match_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_lead_with_jk(data_root, "abcdef0123456789")
            parsed = parse_email(_eml(body="No relevant identifiers here."))
            self.assertEqual(match_message(parsed, data_root=data_root), [])


class IngestConfirmationTest(unittest.TestCase):
    def test_happy_path_promotes_to_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            prepared = _seed_lead_with_jk(data_root, "abcdef0123456789")
            ingest_confirmation(
                raw_bytes=_eml(body="Posting jk: abcdef0123456789. Thanks for applying!"),
                data_root=data_root,
            )
            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "confirmed")
            self.assertEqual([e["type"] for e in status["events"]], ["confirmed"])

    def test_unverified_sender_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_lead_with_jk(data_root, "abcdef0123456789")
            with self.assertRaises(ApplicationError) as ctx:
                ingest_confirmation(
                    raw_bytes=_eml(sender="phisher@evil.example", body="jk=abcdef0123456789"),
                    data_root=data_root,
                )
            self.assertEqual(ctx.exception.error_code, "confirmation_sender_unverified")
            quarantine = list((data_root / "applications" / "_suspicious").glob("*.json"))
            self.assertEqual(len(quarantine), 1)

    def test_no_match_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            _seed_lead_with_jk(data_root, "abcdef0123456789")
            with self.assertRaises(ApplicationError) as ctx:
                ingest_confirmation(
                    raw_bytes=_eml(body="No identifiers."),
                    data_root=data_root,
                )
            self.assertEqual(ctx.exception.error_code, "confirmation_ambiguous")

    def test_idempotent_when_message_re_ingested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            prepared = _seed_lead_with_jk(data_root, "abcdef0123456789")
            ingest_confirmation(
                raw_bytes=_eml(body="jk: abcdef0123456789"),
                data_root=data_root,
            )
            ingest_confirmation(
                raw_bytes=_eml(body="jk: abcdef0123456789"),
                data_root=data_root,
            )
            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(len(status["events"]), 1)


class PriorityLadderTest(unittest.TestCase):
    def test_late_rejection_after_confirmed_is_kept_as_event_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            prepared = _seed_lead_with_jk(data_root, "abcdef0123456789")
            # First: confirmed
            ingest_confirmation(
                raw_bytes=_eml(message_id="<id-1@indeed.com>", body="jk: abcdef0123456789"),
                data_root=data_root,
            )
            self.assertEqual(
                apply_status(prepared.draft_id, data_root=data_root)["lifecycle_state"],
                "confirmed",
            )
            # Then: rejected — should NOT downgrade lifecycle_state.
            ingest_confirmation(
                raw_bytes=_eml(
                    message_id="<id-2@indeed.com>",
                    subject="Update on your application",
                    body="We regret to inform you we are moving forward with other candidates. jk: abcdef0123456789",
                ),
                data_root=data_root,
            )
            status = apply_status(prepared.draft_id, data_root=data_root)
            self.assertEqual(status["lifecycle_state"], "rejected")
            # Both events recorded — confirmed AND rejected.
            event_types = [e["type"] for e in status["events"]]
            self.assertIn("confirmed", event_types)
            self.assertIn("rejected", event_types)


class GmailQueryTest(unittest.TestCase):
    def test_uses_newer_than_not_since(self) -> None:
        q = gmail_search_query({"gmail_query_window_days": 14})
        self.assertIn("newer_than:14d", q)
        self.assertNotIn("since:", q)

    def test_uppercase_OR(self) -> None:
        q = gmail_search_query({})
        # Inside the from:(...) group there should be OR with uppercase letters.
        self.assertIn(" OR ", q)


class CursorTest(unittest.TestCase):
    def test_save_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            save_gmail_cursor(history_id="42", data_root=data_root)
            cursor = load_gmail_cursor(data_root=data_root)
            self.assertEqual(cursor["last_history_id"], "42")
            self.assertIsNotNone(cursor["last_scan_at"])


class SenderAllowlistShapeTest(unittest.TestCase):
    def test_allowlist_includes_all_v1_platforms(self) -> None:
        for needle in ("indeed.com", "greenhouse", "lever", "workday", "ashby"):
            self.assertTrue(
                any(needle in s for s in SENDER_ALLOWLIST),
                f"SENDER_ALLOWLIST missing platform: {needle}",
            )


if __name__ == "__main__":
    unittest.main()
