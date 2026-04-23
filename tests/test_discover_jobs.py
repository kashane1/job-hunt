"""End-to-end tests for `discover_jobs` orchestration + CLI wiring.

All HTTP is stubbed via patch of `job_hunt.discovery.fetch` so no real
network I/O happens in CI. We seed small Greenhouse/Lever/careers fixtures
and assert the bucket counts, cursor advancement, provenance appends, and
CLI round-trips.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.core import main
from job_hunt.discovery import (
    DiscoveryConfig,
    DiscoveryError,
    _cursor_key,
    discover_jobs,
    load_cursor,
    promote_review_entry,
    write_review_entry,
)
from job_hunt.ingestion import FetchResult


FIXTURES = ROOT / "tests" / "fixtures" / "discovery"


def _make_fetch_stub(response_map: dict[str, tuple[int, str]]):
    """Build a fake `fetch` that routes by URL substring to a FetchResult.

    The first key whose substring appears in the requested URL wins. Missing
    routes raise a simulated 404 via IngestionError.
    """
    from job_hunt.ingestion import IngestionError

    def fake_fetch(url, *, timeout=10, max_bytes=0, max_decompressed_bytes=0):
        for hint, (status, body) in response_map.items():
            if hint in url:
                return FetchResult(status=status, headers={}, body=body)
        raise IngestionError("not found", error_code="not_found", url=url)

    return fake_fetch


def _write_watchlist(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class DiscoverJobsEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.watchlist_path = self.root / "config" / "watchlist.yaml"
        self.leads_dir = self.root / "data" / "leads"
        self.discovery_root = self.root / "data" / "discovery"
        self.leads_dir.mkdir(parents=True)
        _write_watchlist(
            self.watchlist_path,
            """
companies:
  - name: "ExampleGH"
    greenhouse: "examplegh"
  - name: "AnotherCorp"
    lever: "anothercorp"

filters:
  keywords_any:
    - "engineer"
""",
        )

    def _patched_fetch(self):
        gh_body = (FIXTURES / "greenhouse-board-valid.json").read_text(encoding="utf-8")
        # Per-posting Greenhouse detail fetch used by ingest_url
        gh_detail = json.dumps({
            "title": "Senior Backend Engineer",
            "company_name": "ExampleGH",
            "location": {"name": "Remote - US"},
            "content": "<p>A role</p>",
            "pay_input_ranges": [],
        })
        lever_body = (FIXTURES / "lever-board-valid.json").read_text(encoding="utf-8")
        lever_detail = json.dumps({
            "text": "Senior Product Engineer",
            "categories": {"location": "Remote"},
            "descriptionPlain": "Role description",
        })
        return _make_fetch_stub({
            "/v1/boards/examplegh/jobs/4100001": (200, gh_detail),
            "/v1/boards/examplegh/jobs/4100002": (200, gh_detail),
            "/v1/boards/examplegh/jobs": (200, gh_body),
            "/v0/postings/anothercorp/abc": (200, lever_detail),
            "/v0/postings/anothercorp/999": (200, lever_detail),
            "/v0/postings/anothercorp": (200, lever_body),
        })

    def test_end_to_end_mixed_sources(self) -> None:
        profile = {"preferences": {"target_titles": ["engineer"]}}
        with patch("job_hunt.discovery.fetch", side_effect=self._patched_fetch()), \
             patch("job_hunt.ingestion.fetch", side_effect=self._patched_fetch()):
            result = discover_jobs(
                self.watchlist_path, self.leads_dir, self.discovery_root,
                DiscoveryConfig(
                    sources=("greenhouse", "lever"),
                    auto_score=False,
                    candidate_profile=profile,
                ),
            )
        payload = result.to_dict()
        self.assertGreaterEqual(payload["counts"]["discovered"], 2)
        # Leads written with discovered_via populated
        lead_files = list(self.leads_dir.glob("*.json"))
        self.assertTrue(lead_files)
        for p in lead_files:
            lead = json.loads(p.read_text())
            self.assertIn("discovered_via", lead)
            self.assertGreaterEqual(len(lead["discovered_via"]), 1)
            self.assertIn("primary_source", lead)
            self.assertIn("observed_sources", lead)
            self.assertGreaterEqual(len(lead["observed_sources"]), 1)
        # History artifact written
        history = list((self.discovery_root / "history").glob("*.json"))
        self.assertEqual(len(history), 1)
        # Cursor advanced for complete sources
        cursor = load_cursor(self.discovery_root / "state.json")
        keys = set(cursor["entries"].keys())
        self.assertIn(_cursor_key("ExampleGH", "greenhouse"), keys)
        self.assertIn(_cursor_key("AnotherCorp", "lever"), keys)

    def test_dry_run_no_disk_writes(self) -> None:
        with patch("job_hunt.discovery.fetch", side_effect=self._patched_fetch()), \
             patch("job_hunt.ingestion.fetch", side_effect=self._patched_fetch()):
            discover_jobs(
                self.watchlist_path, self.leads_dir, self.discovery_root,
                DiscoveryConfig(
                    sources=("greenhouse",),
                    dry_run=True,
                    auto_score=False,
                ),
            )
        # No leads; no cursor file; no history artifact.
        self.assertEqual(list(self.leads_dir.glob("*.json")), [])
        self.assertFalse((self.discovery_root / "state.json").exists())
        history_dir = self.discovery_root / "history"
        self.assertFalse(history_dir.exists() and list(history_dir.glob("*.json")))

    def test_max_ingest_cursor_unchanged(self) -> None:
        with patch("job_hunt.discovery.fetch", side_effect=self._patched_fetch()), \
             patch("job_hunt.ingestion.fetch", side_effect=self._patched_fetch()):
            result = discover_jobs(
                self.watchlist_path, self.leads_dir, self.discovery_root,
                DiscoveryConfig(
                    sources=("greenhouse",),
                    max_ingest=0,   # force budget exhaustion
                    auto_score=False,
                ),
            )
        # Budget exhaustion should persist a resumable partial state rather than
        # pretending the source completed cleanly.
        cursor = load_cursor(self.discovery_root / "state.json")
        state = cursor["entries"][_cursor_key("ExampleGH", "greenhouse")]
        self.assertEqual(state["last_run_status"], "partial")
        self.assertNotIn("next_cursor", state)

    def test_idempotent_second_run(self) -> None:
        with patch("job_hunt.discovery.fetch", side_effect=self._patched_fetch()), \
             patch("job_hunt.ingestion.fetch", side_effect=self._patched_fetch()):
            discover_jobs(
                self.watchlist_path, self.leads_dir, self.discovery_root,
                DiscoveryConfig(sources=("greenhouse",), auto_score=False),
            )
            lead_count_after_first = len(list(self.leads_dir.glob("*.json")))
            result2 = discover_jobs(
                self.watchlist_path, self.leads_dir, self.discovery_root,
                DiscoveryConfig(sources=("greenhouse",), auto_score=False),
            )
        # Second run should not create more lead files; dedupes to already_known
        self.assertEqual(
            len(list(self.leads_dir.glob("*.json"))),
            lead_count_after_first,
        )
        self.assertGreater(result2.to_dict()["counts"]["already_known"], 0)

    def test_usajobs_partial_cursor_round_trip(self) -> None:
        self.watchlist_path.write_text(
            """
companies:
  - name: "Federal"
    usajobs_search_profile: "federal_remote"
usajobs_profiles:
  - name: "federal_remote"
    keyword: "platform engineer"
    location_name: "Washington, District of Columbia"
    results_per_page: 25
    who_may_apply: "Public"
    fields: "Full"
""",
            encoding="utf-8",
        )
        page_1 = (FIXTURES / "usajobs-search-page-1.json").read_text(encoding="utf-8")
        page_2 = (FIXTURES / "usajobs-search-page-2.json").read_text(encoding="utf-8")

        def usajobs_fetch(url, **kwargs):
            if "Page=2" in url:
                return FetchResult(status=200, headers={"content-type": "application/json"}, body=page_2)
            if "Page=1" in url:
                return FetchResult(status=200, headers={"content-type": "application/json"}, body=page_1)
            return FetchResult(
                status=200,
                headers={"content-type": "text/html"},
                body="<html><title>USAJOBS posting</title><main>Federal role</main></html>",
            )

        with patch.dict("os.environ", {
            "USAJOBS_API_KEY": "secret",
            "USAJOBS_USER_AGENT_EMAIL": "person@example.com",
        }, clear=False), \
            patch("job_hunt.discovery_providers.usajobs.fetch", side_effect=usajobs_fetch), \
            patch("job_hunt.ingestion.fetch", side_effect=usajobs_fetch):
            discover_jobs(
                self.watchlist_path,
                self.leads_dir,
                self.discovery_root,
                DiscoveryConfig(sources=("usajobs",), auto_score=False),
            )
            cursor = load_cursor(self.discovery_root / "state.json")
            state = cursor["entries"][_cursor_key("Federal", "usajobs")]
            self.assertEqual(state["last_run_status"], "partial")
            self.assertEqual(state["next_cursor"], "2")

            discover_jobs(
                self.watchlist_path,
                self.leads_dir,
                self.discovery_root,
                DiscoveryConfig(sources=("usajobs",), auto_score=False),
            )
        cursor = load_cursor(self.discovery_root / "state.json")
        state = cursor["entries"][_cursor_key("Federal", "usajobs")]
        self.assertEqual(state["last_run_status"], "complete")
        self.assertNotIn("next_cursor", state)


class CliDiscoverySmokeTest(unittest.TestCase):
    """Smoke-test the CLI dispatchers without exercising HTTP."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.watchlist = self.root / "watchlist.yaml"
        self.discovery_root = self.root / "discovery"
        self.leads_dir = self.root / "leads"
        self.leads_dir.mkdir()

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_watchlist_add_show_remove(self) -> None:
        code, out = self._run([
            "watchlist-add", "--watchlist", str(self.watchlist),
            "--name", "CoX", "--greenhouse", "cox",
        ])
        self.assertEqual(code, 0, out)
        code, out = self._run(["watchlist-show", "--watchlist", str(self.watchlist)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["companies"][0]["name"], "CoX")
        code, out = self._run([
            "watchlist-remove", "--watchlist", str(self.watchlist), "--name", "CoX",
        ])
        self.assertEqual(code, 0, out)

    def test_watchlist_add_duplicate_errors(self) -> None:
        self._run([
            "watchlist-add", "--watchlist", str(self.watchlist),
            "--name", "Dup", "--greenhouse", "d",
        ])
        code, out = self._run([
            "watchlist-add", "--watchlist", str(self.watchlist),
            "--name", "Dup", "--greenhouse", "d",
        ])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error_code"], "watchlist_entry_exists")

    def test_watchlist_validate(self) -> None:
        self.watchlist.write_text(
            """
companies:
  - name: "CoX"
    greenhouse: "cox"
""",
            encoding="utf-8",
        )
        code, out = self._run(["watchlist-validate", "--watchlist", str(self.watchlist)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["valid"])

    def test_review_list_empty(self) -> None:
        code, out = self._run(["review-list", "--discovery-root", str(self.discovery_root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"entries": []})

    def test_review_dismiss_roundtrip(self) -> None:
        review_dir = self.discovery_root / "review"
        entry_id = "c" * 16
        write_review_entry(
            review_dir,
            entry_id=entry_id,
            candidate_url="https://co.test/jobs/x",
            anchor_text="Senior Engineer",
            signals=["role_word"],
            source_page="https://co.test/",
            watchlist_company="Co",
        )
        code, out = self._run([
            "review-dismiss", entry_id,
            "--discovery-root", str(self.discovery_root),
            "--reason", "off topic",
        ])
        self.assertEqual(code, 0, out)
        text = (review_dir / f"{entry_id}.md").read_text(encoding="utf-8")
        self.assertIn('status: "dismissed"', text)
        self.assertIn("off topic", text)

    def test_discovery_state_empty(self) -> None:
        code, out = self._run([
            "discovery-state", "--discovery-root", str(self.discovery_root),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"entries": []})

    def test_robots_cache_clear(self) -> None:
        cache = self.discovery_root / "robots_cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_text('{"schema_version": 1, "entries": {}}', encoding="utf-8")
        code, out = self._run([
            "robots-cache-clear", "--discovery-root", str(self.discovery_root),
        ])
        self.assertEqual(code, 0)
        self.assertFalse(cache.exists())

    def test_review_promote_rejects_bad_entry_id(self) -> None:
        code, out = self._run([
            "review-promote", "../evil",
            "--discovery-root", str(self.discovery_root),
            "--leads-dir", str(self.leads_dir),
        ])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error_code"], "review_schema_invalid")

    def test_discover_jobs_cursor_reset_bad_format(self) -> None:
        self.watchlist.write_text("companies:\n  - name: \"CoX\"\n    greenhouse: \"cox\"\n", encoding="utf-8")
        import argparse
        with self.assertRaises(SystemExit):
            main([
                "discover-jobs",
                "--watchlist", str(self.watchlist),
                "--leads-dir", str(self.leads_dir),
                "--discovery-root", str(self.discovery_root),
                "--reset-cursor", "missing-pipe",
                "--no-score",
                "--dry-run",
            ])


if __name__ == "__main__":
    unittest.main()
