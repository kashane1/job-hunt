"""Batch 3 discovery tests — starts with Phase 2 board fetchers.

Network is fully stubbed via `unittest.mock.patch` on `job_hunt.discovery.fetch`
so no real HTTP ever happens in CI.
"""

from __future__ import annotations

import json
import sys
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
    discover_greenhouse_board,
    discover_lever_board,
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


if __name__ == "__main__":
    unittest.main()
