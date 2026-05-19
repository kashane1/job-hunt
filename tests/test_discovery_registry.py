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
    ashby = "exampleco"
    workable = "exampleco"
    smartrecruiters = "exampleco"
    recruitee = "exampleco"
    personio = "exampleco"
    indeed_search_url = "https://www.indeed.com/jobs?q=exampleco"
    remotive_search = "platform engineer"
    careers_url = "https://example.com/careers"
    usajobs_search_profile = "federal_remote_platform"
    usajobs_profile = object()


class DiscoveryRegistryTest(unittest.TestCase):
    def test_all_expected_providers_are_registered(self) -> None:
        for name in (
            "greenhouse", "lever", "indeed_search", "careers", "ashby",
            "workable", "smartrecruiters", "recruitee", "personio", "usajobs",
            "remotive",
        ):
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

    @patch("job_hunt.discovery_providers.ashby.discover_ashby_board")
    def test_ashby_provider_returns_empty_when_slug_missing(self, mock_fetch) -> None:
        provider = get_discovery_provider("ashby")
        company = type("NoAshby", (), {"name": "NoAshby", "ashby": ""})()
        page = provider.list_entries(company, rate_limiter=object())
        self.assertEqual(page.entries, ())
        mock_fetch.assert_not_called()

    @patch("job_hunt.discovery_providers.workable.discover_workable_account")
    def test_workable_provider_delegates_to_fetcher(self, mock_fetch) -> None:
        mock_fetch.return_value = ([
            ListingEntry(
                title="Engineer",
                location="Remote",
                posting_url="https://exampleco.workable.com/jobs/1",
                source="workable",
                source_company="exampleco",
                internal_id="1",
                updated_at="2026-04-20T00:00:00Z",
            )
        ], False)
        provider = get_discovery_provider("workable")
        page = provider.list_entries(StubCompany(), rate_limiter=object())
        self.assertEqual(len(page.entries), 1)
        self.assertFalse(page.truncated)

    @patch("job_hunt.discovery_providers.smartrecruiters.discover_smartrecruiters_company")
    def test_smartrecruiters_provider_delegates_to_fetcher(self, mock_fetch) -> None:
        mock_fetch.return_value = ([
            ListingEntry(
                title="Engineer",
                location="Remote",
                posting_url="https://jobs.smartrecruiters.com/exampleco/123",
                source="smartrecruiters",
                source_company="exampleco",
                internal_id="123",
                updated_at="2026-04-20T00:00:00Z",
            )
        ], False)
        provider = get_discovery_provider("smartrecruiters")
        page = provider.list_entries(StubCompany(), rate_limiter=object())
        self.assertEqual(len(page.entries), 1)
        self.assertFalse(page.truncated)

    def test_smartrecruiters_provider_returns_empty_when_slug_missing(self) -> None:
        provider = get_discovery_provider("smartrecruiters")
        company = type("NoSR", (), {"name": "NoSR", "smartrecruiters": ""})()
        page = provider.list_entries(company, rate_limiter=object())
        self.assertEqual(page.entries, ())

    @patch("job_hunt.discovery_providers.recruitee.discover_recruitee_account")
    def test_recruitee_provider_delegates_to_fetcher(self, mock_fetch) -> None:
        mock_fetch.return_value = ([
            ListingEntry(
                title="Engineer",
                location="Remote",
                posting_url="https://exampleco.recruitee.com/o/eng",
                source="recruitee",
                source_company="exampleco",
                internal_id="1",
                updated_at="2026-04-20T00:00:00Z",
            )
        ], False)
        provider = get_discovery_provider("recruitee")
        page = provider.list_entries(StubCompany(), rate_limiter=object())
        self.assertEqual(len(page.entries), 1)
        self.assertFalse(page.truncated)

    def test_recruitee_provider_returns_empty_when_slug_missing(self) -> None:
        provider = get_discovery_provider("recruitee")
        company = type("NoRec", (), {"name": "NoRec", "recruitee": ""})()
        page = provider.list_entries(company, rate_limiter=object())
        self.assertEqual(page.entries, ())

    @patch("job_hunt.discovery_providers.personio.discover_personio_company")
    def test_personio_provider_delegates_to_fetcher(self, mock_fetch) -> None:
        mock_fetch.return_value = ([
            ListingEntry(
                title="Engineer",
                location="Munich",
                posting_url="https://exampleco.jobs.personio.com/job/1",
                source="personio",
                source_company="exampleco",
                internal_id="1",
                updated_at="2026-04-20",
            )
        ], False)
        provider = get_discovery_provider("personio")
        page = provider.list_entries(StubCompany(), rate_limiter=object())
        self.assertEqual(len(page.entries), 1)
        self.assertFalse(page.truncated)

    def test_personio_provider_returns_empty_when_slug_missing(self) -> None:
        provider = get_discovery_provider("personio")
        company = type("NoPer", (), {"name": "NoPer", "personio": ""})()
        page = provider.list_entries(company, rate_limiter=object())
        self.assertEqual(page.entries, ())

    @patch("job_hunt.discovery_providers.remotive.discover_remotive_search")
    def test_remotive_provider_delegates_and_rewrites_company(self, mock_fetch) -> None:
        mock_fetch.return_value = ([
            ListingEntry(
                title="Engineer",
                location="Worldwide",
                posting_url="https://remotive.com/remote-jobs/x-1",
                source="remotive",
                source_company="remotive",
                internal_id="1",
                updated_at="2026-04-20T00:00:00",
                employer_name="RealemployerCo",
            )
        ], False)
        provider = get_discovery_provider("remotive")
        page = provider.list_entries(
            StubCompany(), rate_limiter=object(), watchlist_company="My Remotive Search"
        )
        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].source_company, "My Remotive Search")
        self.assertEqual(page.entries[0].employer_name, "RealemployerCo")

    def test_remotive_provider_returns_empty_when_query_missing(self) -> None:
        provider = get_discovery_provider("remotive")
        company = type("NoRemotive", (), {"name": "NoRemotive", "remotive_search": ""})()
        page = provider.list_entries(company, rate_limiter=object())
        self.assertEqual(page.entries, ())

    @patch("job_hunt.discovery_providers.usajobs.discover_usajobs_profile")
    def test_usajobs_provider_passes_cursor(self, mock_fetch) -> None:
        mock_fetch.return_value = ([], False, "2")
        provider = get_discovery_provider("usajobs")
        page = provider.list_entries(StubCompany(), rate_limiter=object(), cursor="1")
        self.assertEqual(page.next_cursor, "2")
        mock_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
