from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.discovery import ListingEntry
from job_hunt.discovery_providers.registry import get_discovery_provider


class StubCompany:
    name = "ExampleCo"
    greenhouse = "exampleco"
    lever = "exampleco"
    indeed_search_url = "https://www.indeed.com/jobs?q=exampleco"
    careers_url = "https://example.com/careers"


class DiscoveryRegistryTest(unittest.TestCase):
    def test_all_expected_providers_are_registered(self) -> None:
        for name in ("greenhouse", "lever", "indeed_search", "careers"):
            self.assertIsNotNone(get_discovery_provider(name))

    @patch("job_hunt.discovery.discover_greenhouse_board")
    def test_greenhouse_provider_delegates_to_existing_fetcher(self, mock_fetch) -> None:
        mock_fetch.return_value = ([
            ListingEntry(
                title="Engineer",
                location="Remote",
                posting_url="https://boards.greenhouse.io/exampleco/jobs/1",
                source="greenhouse",
                source_company="ExampleCo",
                internal_id="1",
                updated_at="2026-04-20T00:00:00Z",
            )
        ], False)
        provider = get_discovery_provider("greenhouse")
        page = provider.list_entries(StubCompany(), rate_limiter=object())
        self.assertEqual(len(page.entries), 1)
        self.assertFalse(page.truncated)

    @patch("job_hunt.discovery.discover_company_careers")
    def test_careers_provider_preserves_low_confidence_and_ats_hits(self, mock_crawl) -> None:
        mock_crawl.return_value = type("Crawl", (), {
            "high_confidence": (),
            "ats_hits": [("greenhouse", "https://boards.greenhouse.io/exampleco")],
            "low_confidence": [{
                "candidate_url": "https://example.com/jobs/1",
                "anchor_text": "Apply",
                "signals": ["path_hint", "text_hint"],
                "source_page": "https://example.com/careers",
            }],
        })()
        provider = get_discovery_provider("careers")
        page = provider.list_entries(StubCompany(), rate_limiter=object(), robots=object())
        self.assertEqual(page.ats_hits[0][0], "greenhouse")
        self.assertEqual(page.low_confidence[0].candidate_url, "https://example.com/jobs/1")


if __name__ == "__main__":
    unittest.main()
