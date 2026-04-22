"""Tests for the humanize layer.

Six tests per plan Acceptance Criteria:
1. Seeded determinism — byte-identical bundle across two runs.
2. Disabled policy returns minimal bundle.
3. Read-time clamps to [min_ms, max_ms].
4. _lognormal_params_from_moments preserves empirical mean/stddev (deterministic).
5. validate_humanize_plan re-clamps tampered values.
6. Playbook grep invariant — no auto-submit patterns in playbooks.
"""

from __future__ import annotations

import copy
import json
import random
import re
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt import humanize
from job_hunt.humanize import (
    HUMANIZE_DEFAULTS,
    _lognormal_params_from_moments,
    build_humanize_plan,
    redact_humanize_for_audit,
    seed_from_draft_id,
    validate_humanize_plan,
)


def _policy() -> dict:
    return copy.deepcopy(HUMANIZE_DEFAULTS)


def _fields(n: int = 3) -> list[dict]:
    return [
        {"field_id": f"f{i}", "question_text": f"Question {i}?",
         "question": f"Question {i}?", "answer": f"answer text number {i}"}
        for i in range(n)
    ]


def _page_info(words: int = 240) -> dict:
    return {"visible_text_word_count": words, "has_scrollable_body": True}


class SeededDeterminismTest(unittest.TestCase):
    def test_seeded_output_is_byte_identical_across_two_runs(self) -> None:
        seed = seed_from_draft_id("indeed-lead-001-apply-abcdef12")
        plan_a = build_humanize_plan(
            _fields(), _page_info(), _policy(), rng=random.Random(seed),
        )
        plan_b = build_humanize_plan(
            _fields(), _page_info(), _policy(), rng=random.Random(seed),
        )
        self.assertEqual(json.dumps(plan_a, sort_keys=True),
                         json.dumps(plan_b, sort_keys=True))


class DisabledPolicyTest(unittest.TestCase):
    def test_disabled_policy_returns_minimal_bundle(self) -> None:
        policy = _policy()
        policy["enabled"] = False
        plan = build_humanize_plan(
            _fields(), _page_info(), policy, rng=random.Random(0),
        )
        self.assertEqual(plan, {"enabled": False})


class ClampTest(unittest.TestCase):
    def test_read_time_clamps_to_min_and_max(self) -> None:
        policy = _policy()
        policy["read"]["min_ms"] = 1000
        policy["read"]["max_ms"] = 1500
        # Try a wide range of seeds and word counts; result must always
        # fall in the clamp window.
        for seed in range(20):
            for words in (0, 5, 200, 5000, 100_000):
                ms = humanize._sample_read_time_ms(
                    words, policy, rng=random.Random(seed),
                )
                self.assertGreaterEqual(ms, 1000)
                self.assertLessEqual(ms, 1500)


class LognormalMomentsTest(unittest.TestCase):
    def test_lognormal_params_from_moments_preserves_empirical_mean_stddev(self) -> None:
        target_mean = 170.0
        target_stddev = 65.0
        mu, sigma = _lognormal_params_from_moments(target_mean, target_stddev)
        rng = random.Random(20260420)
        samples = [rng.lognormvariate(mu, sigma) for _ in range(20_000)]
        empirical_mean = statistics.fmean(samples)
        empirical_stddev = statistics.stdev(samples)
        # Tight tolerances given the seed + sample size.
        self.assertAlmostEqual(empirical_mean, target_mean, delta=4.0)
        self.assertAlmostEqual(empirical_stddev, target_stddev, delta=4.0)

    def test_lognormal_params_rejects_nonpositive_mean(self) -> None:
        with self.assertRaises(ValueError):
            _lognormal_params_from_moments(0.0, 1.0)
        with self.assertRaises(ValueError):
            _lognormal_params_from_moments(-1.0, 1.0)


class ValidateClampTest(unittest.TestCase):
    def test_validate_humanize_plan_reclamps_tampered_values(self) -> None:
        plan = build_humanize_plan(
            _fields(2), _page_info(), _policy(), rng=random.Random(42),
        )
        # Simulate bundle tampering: extreme jd_read_ms, page_advance, per-field.
        plan["jd_read_ms"] = 999_999_999
        plan["page_advance"]["pre_click_ms"] = 999_999_999
        plan["per_field"][0]["pre_read_ms"] = 999_999_999
        plan["per_field"][0]["typing"]["chunk_delay_ms"] = [999_999_999]

        clamped = validate_humanize_plan(plan, _policy())
        self.assertLessEqual(clamped["jd_read_ms"], 60_000)
        self.assertLessEqual(clamped["page_advance"]["pre_click_ms"], 60_000)
        self.assertLessEqual(clamped["per_field"][0]["pre_read_ms"], 60_000)
        for delay in clamped["per_field"][0]["typing"]["chunk_delay_ms"]:
            self.assertLessEqual(delay, 60_000)


class GrepInvariantTest(unittest.TestCase):
    def test_playbooks_contain_no_auto_submit_patterns(self) -> None:
        """Hard floor: no playbook may instruct the agent to click Submit.

        The human-submit invariant is repo policy (AGENTS.md). This grep is
        cheap insurance against a future contributor who reads the
        humanization plan and reasons "it's so realistic now we can submit
        for them too."
        """
        repo_root = Path(__file__).resolve().parents[1]
        playbook_dir = repo_root / "playbooks" / "application"
        bad_pattern = re.compile(r"click[^\n]{0,40}submit", re.IGNORECASE)
        # Allowed surrounding-word markers that scope the click to the human.
        human_marker = re.compile(
            r"\b(user|human|they|themselves|operator|you click|"
            r"never|do not|don't|forbidden|pause|stop|invariant)\b",
            re.IGNORECASE,
        )
        offenders: list[str] = []
        for playbook in sorted(playbook_dir.glob("*.md")):
            text = playbook.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if bad_pattern.search(line) and not human_marker.search(line):
                    offenders.append(f"{playbook.name}:{line_no}: {line.strip()}")
        self.assertEqual(offenders, [], "Auto-submit patterns found:\n" + "\n".join(offenders))


class TuningTest(unittest.TestCase):
    """Tuning rules surfaced by the Inversion dry-run."""

    def test_url_answer_is_typed_atomically_regardless_of_mode(self) -> None:
        spec = humanize._sample_typing_spec(
            "https://www.linkedin.com/in/kashanesakha",
            _policy(),
            rng=random.Random(0),
        )
        self.assertEqual(spec["mode"], "atomic")
        self.assertEqual(spec["chunk_boundaries"], [])
        self.assertEqual(spec["chunk_delay_ms"], [])

    def test_email_answer_is_typed_atomically(self) -> None:
        spec = humanize._sample_typing_spec(
            "user+tag@example.com", _policy(), rng=random.Random(0),
        )
        self.assertEqual(spec["mode"], "atomic")

    def test_plain_text_answer_still_chunked(self) -> None:
        spec = humanize._sample_typing_spec(
            "hello world from a human", _policy(), rng=random.Random(0),
        )
        self.assertEqual(spec["mode"], "word_chunked")
        self.assertGreater(len(spec["chunk_boundaries"]), 1)

    def test_curated_provenance_lowers_read_time_floor(self) -> None:
        # Curated short-question field: floor drops from default 600ms to 300ms.
        policy = _policy()
        fields = [
            {"question_text": "Remote?", "answer": "Yes", "provenance": "curated"},
            {"question_text": "Remote?", "answer": "Yes", "provenance": "curated_template"},
        ]
        rng = random.Random(7)
        plan = build_humanize_plan(fields, _page_info(0), policy, rng=rng)
        entries = plan["per_field"]
        # With seed=7, the curated field should land below 600 (the default floor)
        # while curated_template still respects the 600 floor.
        self.assertLess(entries[0]["pre_read_ms"], 600)
        self.assertGreaterEqual(entries[1]["pre_read_ms"], 600)


class RedactionTest(unittest.TestCase):
    """Sanity check for redact_humanize_for_audit (used by application.py)."""

    def test_redaction_strips_chunk_arrays(self) -> None:
        plan = build_humanize_plan(
            _fields(2), _page_info(), _policy(), rng=random.Random(7),
        )
        redacted = redact_humanize_for_audit(plan)
        for entry in redacted["per_field"]:
            self.assertNotIn("chunk_boundaries", entry["typing"])
            self.assertNotIn("chunk_delay_ms", entry["typing"])
            self.assertIn("chunk_count", entry["typing"])


if __name__ == "__main__":
    unittest.main()
