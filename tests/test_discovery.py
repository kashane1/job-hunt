"""Batch 3 discovery tests — starts with Phase 2 board fetchers.

Network is fully stubbed via `unittest.mock.patch` on `job_hunt.discovery.fetch`
so no real HTTP ever happens in CI.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.discovery import (
    DISCOVERY_ERROR_CODES,
    DiscoveryError,
    DiscoveryResult,
    ListingEntry,
    Outcome,
    SOURCE_NAME_MAP,
    SourceRun,
    _detect_ats_subdomain_links,
    _extract_jobpostings_from_jsonld,
    _classify_heuristic_link,
    detect_anti_bot,
    discover_company_careers,
    discover_greenhouse_board,
    discover_lever_board,
    write_review_entry,
)
from job_hunt.ingestion import FetchResult, GREENHOUSE_URL_RE, IngestionError, LEVER_URL_RE
from job_hunt.net_policy import DomainRateLimiter


FIXTURES = ROOT / "tests" / "fixtures" / "discovery"


def _fetch_ok(body: str) -> FetchResult:
    return FetchResult(status=200, headers={"content-type": "application/json"}, body=body)


class GreenhouseBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = DomainRateLimiter(default_interval_s=0.0)

    def test_valid_slug_returns_entries(self) -> None:
        body = (FIXTURES / "greenhouse-board-valid.json").read_text(encoding="utf-8")
        with patch("job_hunt.discovery.fetch", return_value=_fetch_ok(body)):
            entries, truncated = discover_greenhouse_board("examplegh", self.limiter)
        self.assertEqual(len(entries), 2)
        self.assertFalse(truncated)
        self.assertEqual(entries[0].title, "Senior Backend Engineer")
        self.assertEqual(entries[0].source, "greenhouse")
        self.assertEqual(entries[0].source_company, "examplegh")
        self.assertEqual(entries[0].confidence, "high")
        self.assertEqual(entries[0].signals, ())
        # URL passes batch-2 ingest_url regex
        self.assertTrue(GREENHOUSE_URL_RE.match(entries[0].posting_url))

    def test_unknown_slug_returns_empty(self) -> None:
        def raise_404(*a, **kw):
            raise IngestionError("not found", error_code="not_found", url="x")

        with patch("job_hunt.discovery.fetch", side_effect=raise_404):
            entries, truncated = discover_greenhouse_board("nope", self.limiter)
        self.assertEqual(entries, [])
        self.assertFalse(truncated)

    def test_http_5xx_propagates(self) -> None:
        def raise_500(*a, **kw):
            raise IngestionError("http 500", error_code="http_error", url="x")

        with patch("job_hunt.discovery.fetch", side_effect=raise_500):
            with self.assertRaises(IngestionError):
                discover_greenhouse_board("anyslug", self.limiter)


class LeverBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = DomainRateLimiter(default_interval_s=0.0)

    def test_valid_slug_returns_entries(self) -> None:
        body = (FIXTURES / "lever-board-valid.json").read_text(encoding="utf-8")
        with patch("job_hunt.discovery.fetch", return_value=_fetch_ok(body)):
            entries, truncated = discover_lever_board("anothercorp", self.limiter)
        self.assertEqual(len(entries), 2)
        self.assertFalse(truncated)
        # Every URL is accepted by batch-2 ingest_url regex
        for e in entries:
            self.assertTrue(LEVER_URL_RE.match(e.posting_url))
        self.assertEqual(entries[0].source, "lever")

    def test_ms_epoch_converted_to_iso(self) -> None:
        body = (FIXTURES / "lever-board-valid.json").read_text(encoding="utf-8")
        with patch("job_hunt.discovery.fetch", return_value=_fetch_ok(body)):
            entries, _ = discover_lever_board("anothercorp", self.limiter)
        # 1733443200000 ms → 2024-12-06T00:00:00+00:00
        self.assertEqual(entries[0].updated_at, "2024-12-06T00:00:00+00:00")

    def test_unknown_slug_returns_empty(self) -> None:
        def raise_404(*a, **kw):
            raise IngestionError("not found", error_code="not_found", url="x")

        with patch("job_hunt.discovery.fetch", side_effect=raise_404):
            entries, _ = discover_lever_board("nope", self.limiter)
        self.assertEqual(entries, [])


class ListingEntryContractsTest(unittest.TestCase):
    def test_high_confidence_for_board_sources(self) -> None:
        body = (FIXTURES / "greenhouse-board-valid.json").read_text(encoding="utf-8")
        with patch("job_hunt.discovery.fetch", return_value=_fetch_ok(body)):
            entries, _ = discover_greenhouse_board("examplegh", DomainRateLimiter(0.0))
        self.assertTrue(all(e.confidence == "high" for e in entries))
        self.assertTrue(all(e.signals == () for e in entries))


class DataTypeShapesTest(unittest.TestCase):
    def test_listing_entry_to_dict_round_trip(self) -> None:
        e = ListingEntry(
            title="T", location="L", posting_url="U", source="greenhouse",
            source_company="c", internal_id="1", updated_at="2026-04-16T00:00:00Z",
            signals=("a",), confidence="high",
        )
        d = e.to_dict()
        self.assertEqual(d["signals"], ["a"])
        self.assertEqual(d["confidence"], "high")

    def test_outcome_to_dict(self) -> None:
        o = Outcome(bucket="discovered", entry=None, detail={"k": "v"})
        self.assertEqual(o.to_dict()["bucket"], "discovered")
        self.assertEqual(o.to_dict()["entry"], None)

    def test_discovery_result_counts_shape(self) -> None:
        r = DiscoveryResult(
            outcomes=[
                Outcome(bucket="discovered", entry=None),
                Outcome(bucket="discovered", entry=None),
                Outcome(bucket="filtered_out", entry=None),
            ],
            sources_run=[],
            run_started_at="2026-04-16T00:00:00+00:00",
            run_completed_at="2026-04-16T00:00:01+00:00",
        )
        d = r.to_dict()
        self.assertEqual(d["counts"]["discovered"], 2)
        self.assertEqual(d["counts"]["filtered_out"], 1)
        self.assertEqual(d["counts"]["failed"], 0)

    def test_source_name_map_consistency(self) -> None:
        for cli_token, (runtime_source, discovered_via) in SOURCE_NAME_MAP.items():
            self.assertIsInstance(cli_token, str)
            self.assertIsInstance(runtime_source, str)
            self.assertIsInstance(discovered_via, str)


class DiscoveryErrorTest(unittest.TestCase):
    def test_subclasses_structured_error(self) -> None:
        from job_hunt.utils import StructuredError
        err = DiscoveryError("m", error_code="anti_bot_blocked", url="x", remediation="y")
        self.assertIsInstance(err, StructuredError)
        self.assertEqual(err.to_dict()["error_code"], "anti_bot_blocked")

    def test_unknown_code_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            DiscoveryError("m", error_code="made_up_code")


# ---------------------------------------------------------------------------
# Phase 3 — career-page crawler
# ---------------------------------------------------------------------------

class _AllowingRobots:
    def can_fetch(self, url: str) -> bool:
        return True


class _BlockingRobots:
    def can_fetch(self, url: str) -> bool:
        return False


class AntiBotDetectionTest(unittest.TestCase):
    def test_body_alone_does_not_trigger(self) -> None:
        r = FetchResult(status=200, headers={}, body="<html>protected by cloudflare</html>")
        self.assertFalse(detect_anti_bot(r))

    def test_status_403_with_cf_ray_triggers(self) -> None:
        r = FetchResult(status=403, headers={"cf-ray": "abc"}, body="")
        self.assertTrue(detect_anti_bot(r))

    def test_status_503_with_just_a_moment_triggers(self) -> None:
        r = FetchResult(status=503, headers={}, body="<title>Just a moment...</title>")
        self.assertTrue(detect_anti_bot(r))

    def test_status_alone_no_signal_does_not_trigger(self) -> None:
        r = FetchResult(status=403, headers={}, body="<p>forbidden</p>")
        self.assertFalse(detect_anti_bot(r))


class JsonLdExtractionTest(unittest.TestCase):
    def test_extracts_jobposting(self) -> None:
        body = (ROOT / "tests" / "fixtures" / "discovery" / "careers-json-ld.html").read_text(
            encoding="utf-8",
        )
        postings = _extract_jobpostings_from_jsonld(body)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0]["title"], "Senior Backend Engineer")

    def test_tolerates_malformed_block(self) -> None:
        body = (
            '<script type="application/ld+json">{ bad json</script>'
            '<script type="application/ld+json">'
            '{"@type": "JobPosting", "title": "T", "url": "https://x/jobs/1"}'
            '</script>'
        )
        postings = _extract_jobpostings_from_jsonld(body)
        self.assertEqual(len(postings), 1)


class AtsSubdomainDetectionTest(unittest.TestCase):
    def test_greenhouse_subdomain_detected(self) -> None:
        body = (ROOT / "tests" / "fixtures" / "discovery" / "careers-ats-subdomain.html").read_text(
            encoding="utf-8",
        )
        hits = _detect_ats_subdomain_links(body, "https://anothercorp.com/careers")
        platforms = {p for p, _ in hits}
        self.assertIn("greenhouse", platforms)
        self.assertIn("lever", platforms)


class HeuristicClassifierTest(unittest.TestCase):
    def test_two_signals_high(self) -> None:
        count, labels = _classify_heuristic_link(
            "https://co.com/careers/senior-engineer",
            "Senior Engineer",
            "<nav><a",
        )
        self.assertGreaterEqual(count, 2)

    def test_one_signal_low(self) -> None:
        count, labels = _classify_heuristic_link(
            "https://co.com/careers/x",
            "Our blog",
            "<p><a",
        )
        self.assertEqual(count, 1)

    def test_zero_signals(self) -> None:
        count, _ = _classify_heuristic_link(
            "https://co.com/blog/a",
            "Hello",
            "<p><a",
        )
        self.assertEqual(count, 0)


class CareerCrawlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = DomainRateLimiter(default_interval_s=0.0)
        self.robots = _AllowingRobots()

    def _patch_fetch(self, body: str, status: int = 200, headers=None):
        return patch(
            "job_hunt.discovery.fetch",
            return_value=FetchResult(
                status=status, headers=headers or {}, body=body,
            ),
        )

    def test_json_ld_high_confidence(self) -> None:
        body = (ROOT / "tests" / "fixtures" / "discovery" / "careers-json-ld.html").read_text(
            encoding="utf-8",
        )
        with self._patch_fetch(body):
            result = discover_company_careers(
                "https://exampleco.com/careers",
                self.limiter, self.robots, watchlist_company="ExampleCo",
            )
        self.assertEqual(len(result.high_confidence), 1)
        self.assertEqual(result.high_confidence[0].confidence, "high")
        self.assertIn("json_ld", result.high_confidence[0].signals)

    def test_ats_subdomain_bypasses_heuristic(self) -> None:
        body = (ROOT / "tests" / "fixtures" / "discovery" / "careers-ats-subdomain.html").read_text(
            encoding="utf-8",
        )
        with self._patch_fetch(body):
            result = discover_company_careers(
                "https://anothercorp.com/careers",
                self.limiter, self.robots, watchlist_company="AnotherCorp",
            )
        self.assertEqual(result.high_confidence, ())
        self.assertEqual(result.low_confidence, ())
        platforms = {p for p, _ in result.ats_hits}
        self.assertIn("greenhouse", platforms)

    def test_two_signal_heuristic_high(self) -> None:
        body = (ROOT / "tests" / "fixtures" / "discovery" / "careers-heuristic-2signal.html").read_text(
            encoding="utf-8",
        )
        with self._patch_fetch(body):
            result = discover_company_careers(
                "https://thirdco.com/opportunities",
                self.limiter, self.robots, watchlist_company="ThirdCo",
            )
        self.assertTrue(len(result.high_confidence) >= 1)
        first = result.high_confidence[0]
        self.assertEqual(first.confidence, "weak_inference")
        self.assertIn("path_hint", first.signals)

    def test_one_signal_goes_to_review(self) -> None:
        body = (ROOT / "tests" / "fixtures" / "discovery" / "careers-heuristic-1signal.html").read_text(
            encoding="utf-8",
        )
        with self._patch_fetch(body):
            result = discover_company_careers(
                "https://company.test/",
                self.limiter, self.robots, watchlist_company="Company",
            )
        self.assertEqual(len(result.high_confidence), 0)
        self.assertTrue(len(result.low_confidence) >= 1)

    def test_robots_disallow_empty_result(self) -> None:
        with patch("job_hunt.discovery.fetch") as mock_fetch:
            result = discover_company_careers(
                "https://blocked.test/careers",
                self.limiter, _BlockingRobots(), watchlist_company="Blocked",
            )
        mock_fetch.assert_not_called()
        self.assertEqual(result.high_confidence, ())

    def test_anti_bot_raises(self) -> None:
        with self._patch_fetch(
            "<title>Just a moment...</title>",
            status=503,
            headers={"cf-ray": "abc"},
        ):
            with self.assertRaises(DiscoveryError) as ctx:
                discover_company_careers(
                    "https://cf.test/",
                    self.limiter, self.robots, watchlist_company="CF",
                )
        self.assertEqual(ctx.exception.error_code, "anti_bot_blocked")

    def test_linkedin_url_is_allowlisted(self) -> None:
        # LinkedIn is allowlisted in config/domain-allowlist.yaml; discovery no
        # longer hard-fails on hard_fail_platform. The call may still raise for
        # other reasons (robots, anti-bot) but not with hard_fail_platform.
        try:
            discover_company_careers(
                "https://linkedin.com/jobs/",
                self.limiter, self.robots, watchlist_company="LI",
            )
        except DiscoveryError as exc:
            self.assertNotEqual(exc.error_code, "hard_fail_platform")


class ReviewFileWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.review_dir = Path(self._tmp.name)

    def test_single_file_with_frontmatter(self) -> None:
        entry_id = "a" * 16
        path = write_review_entry(
            self.review_dir,
            entry_id=entry_id,
            candidate_url="https://co.test/jobs/x",
            anchor_text="Senior Engineer",
            signals=["role_word"],
            source_page="https://co.test/",
            watchlist_company="Co",
        )
        self.assertEqual(path.name, f"{entry_id}.md")
        # No paired .json
        self.assertFalse((self.review_dir / f"{entry_id}.json").exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("DATA_NOT_INSTRUCTIONS: true", text)
        self.assertIn("candidate_url: \"https://co.test/jobs/x\"", text)

    def test_anchor_text_escaped_and_fenced(self) -> None:
        entry_id = "b" * 16
        path = write_review_entry(
            self.review_dir,
            entry_id=entry_id,
            candidate_url="https://co.test/jobs/x",
            anchor_text="<script>alert(1)</script>```bad",
            signals=["role_word"],
            source_page="https://co.test/",
            watchlist_company="Co",
        )
        text = path.read_text(encoding="utf-8")
        # Escaped form
        self.assertIn("&lt;script&gt;", text)
        # Backticks inside user-controlled text are neutralized so the fence
        # close marker (three backticks) never appears inside the block
        # except as the intentional closer.
        between_open_and_close = text.split("```untrusted_data_")[1]
        user_content = between_open_and_close.split("\n```", 1)[1]
        self.assertNotIn("```", user_content)

    def test_entry_id_regex_rejected(self) -> None:
        with self.assertRaises(DiscoveryError) as ctx:
            write_review_entry(
                self.review_dir,
                entry_id="../evil",
                candidate_url="https://co.test/jobs/x",
                anchor_text="a",
                signals=[],
                source_page="https://co.test/",
                watchlist_company="Co",
            )
        self.assertEqual(ctx.exception.error_code, "review_schema_invalid")


class DiscoveryUserAgentTest(unittest.TestCase):
    def test_discovery_user_agent_constant_single_sourced(self) -> None:
        # The UA literal must appear exactly once across src/ — at the
        # module-level definition. Everything else imports by name. Uses AST
        # so implicit-concatenation multi-line string literals collapse to a
        # single constant (grep-based single-file tests cannot handle that).
        import ast
        from job_hunt.discovery import DISCOVERY_USER_AGENT

        src_root = ROOT / "src" / "job_hunt"
        hits: list[str] = []
        for py_file in src_root.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == DISCOVERY_USER_AGENT:
                    hits.append(f"{py_file.relative_to(src_root)}:{node.lineno}")
        self.assertEqual(
            len(hits), 1,
            f"Expected exactly one literal defining DISCOVERY_USER_AGENT, got: {hits}",
        )


if __name__ == "__main__":
    unittest.main()
