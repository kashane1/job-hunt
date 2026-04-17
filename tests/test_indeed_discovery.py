"""Phase 3 tests for ``src/job_hunt/indeed_discovery.py``.

Covers URL parsing, pagination URL construction, JSON-LD extraction, heuristic
extraction, dedup across sources, and the anti-bot short-circuit. Live network
behavior lives in the spike todo (#046); these tests rely on fixtures.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.indeed_discovery import (
    IndeedSearchConfig,
    MAX_PAGES_PER_RUN,
    RESULTS_PER_PAGE,
    discover_indeed_search,
    parse_search_results,
)
from job_hunt.ingestion import FetchResult


JSON_LD_PAGE = """<html><head>
<script type="application/ld+json">
{"@type": "JobPosting",
 "title": "Senior Platform Engineer",
 "url": "https://www.indeed.com/viewjob?jk=abcdef1234567890",
 "hiringOrganization": {"name": "ExampleCo"},
 "jobLocation": {"address": {"addressLocality": "Remote",
                              "addressRegion": "CA",
                              "addressCountry": "US"}}}
</script>
</head><body>cards</body></html>"""


HEURISTIC_PAGE = """<html><body>
<div class="jobsearch-card" data-jk="cafecafecafecafe">
  <h2 class="jobTitle"><span>Senior Backend Engineer</span></h2>
  <span data-testid="company-name">Widgets Inc</span>
  <div data-testid="text-location">Los Angeles, CA</div>
</div>
<div class="jobsearch-card" data-jk="deadbeefdeadbeef">
  <h2 class="jobTitle">Staff Platform Engineer</h2>
  <span data-testid="company-name">Another Corp</span>
  <div data-testid="text-location">Remote</div>
</div>
</body></html>"""


CLOUDFLARE_CHALLENGE = FetchResult(
    status=503,
    headers={"cf-ray": "abc123", "content-type": "text/html"},
    body="<html><head><title>Just a moment...</title></head></html>",
)


class IndeedSearchConfigTest(unittest.TestCase):
    def test_parses_basic_url(self) -> None:
        cfg = IndeedSearchConfig.from_url(
            "https://www.indeed.com/jobs?q=senior+platform&l=Remote"
        )
        self.assertEqual(cfg.query, "senior platform")
        self.assertEqual(cfg.location, "Remote")
        self.assertEqual(cfg.start, 0)

    def test_parses_radius(self) -> None:
        cfg = IndeedSearchConfig.from_url(
            "https://www.indeed.com/jobs?q=engineer&l=LA&radius=25"
        )
        self.assertEqual(cfg.radius, 25)

    def test_rejects_non_indeed_url(self) -> None:
        with self.assertRaises(ValueError):
            IndeedSearchConfig.from_url("https://boards.greenhouse.io/jobs?q=x")

    def test_page_url_advances_start_by_results_per_page(self) -> None:
        cfg = IndeedSearchConfig.from_url(
            "https://www.indeed.com/jobs?q=python&l=Remote"
        )
        # RESULTS_PER_PAGE is 10 per Indeed's own pagination.
        self.assertIn("start=0", cfg.page_url(0))
        self.assertIn(f"start={RESULTS_PER_PAGE}", cfg.page_url(1))
        self.assertIn(f"start={RESULTS_PER_PAGE * 3}", cfg.page_url(3))


class ParseSearchResultsTest(unittest.TestCase):
    def test_parses_json_ld_posting(self) -> None:
        postings = parse_search_results(JSON_LD_PAGE)
        self.assertEqual(len(postings), 1)
        p = postings[0]
        self.assertEqual(p.jk, "abcdef1234567890")
        self.assertEqual(p.title, "Senior Platform Engineer")
        self.assertEqual(p.company, "ExampleCo")
        self.assertIn("Remote", p.location)
        self.assertEqual(p.source_signal, "json_ld")

    def test_parses_heuristic_cards(self) -> None:
        postings = parse_search_results(HEURISTIC_PAGE)
        jks = sorted(p.jk for p in postings)
        self.assertEqual(jks, ["cafecafecafecafe", "deadbeefdeadbeef"])
        by_jk = {p.jk: p for p in postings}
        self.assertEqual(by_jk["cafecafecafecafe"].title, "Senior Backend Engineer")
        self.assertEqual(by_jk["cafecafecafecafe"].company, "Widgets Inc")
        self.assertEqual(by_jk["cafecafecafecafe"].location, "Los Angeles, CA")
        self.assertEqual(by_jk["deadbeefdeadbeef"].source_signal, "heuristic")

    def test_json_ld_wins_over_heuristic_dedup(self) -> None:
        # Same jk in both JSON-LD and data-jk card → only one entry.
        combined = JSON_LD_PAGE.replace(
            "<body>cards</body>",
            '<body><div data-jk="abcdef1234567890">'
            '<h2 class="jobTitle">duplicate title</h2></div></body>',
        )
        postings = parse_search_results(combined)
        self.assertEqual(len(postings), 1)
        self.assertEqual(postings[0].source_signal, "json_ld")

    def test_empty_body_returns_empty(self) -> None:
        self.assertEqual(parse_search_results("<html><body></body></html>"), [])


class DiscoverIndeedSearchTest(unittest.TestCase):
    def test_cap_stops_pagination(self) -> None:
        # Build a fake page with 10 cards; cap at 5 → truncated + 5 entries.
        cards = "".join(
            f'<div data-jk="{i:016x}"><h2 class="jobTitle">Role {i}</h2>'
            f'<span data-testid="company-name">Co{i}</span>'
            f'<div data-testid="text-location">Remote</div></div>'
            for i in range(10)
        )
        body = f"<html><body>{cards}</body></html>"
        rate_limiter = MagicMock()
        with patch(
            "job_hunt.indeed_discovery.fetch",
            return_value=FetchResult(status=200, headers={}, body=body),
        ):
            entries, truncated = discover_indeed_search(
                "https://www.indeed.com/jobs?q=x",
                rate_limiter,
                result_cap=5,
                max_pages=3,
            )
        self.assertEqual(len(entries), 5)
        self.assertTrue(truncated)

    def test_anti_bot_raises_discovery_error(self) -> None:
        from job_hunt.discovery import DiscoveryError

        rate_limiter = MagicMock()
        with patch(
            "job_hunt.indeed_discovery.fetch",
            return_value=CLOUDFLARE_CHALLENGE,
        ):
            with self.assertRaises(DiscoveryError) as ctx:
                discover_indeed_search(
                    "https://www.indeed.com/jobs?q=x",
                    rate_limiter,
                )
            self.assertEqual(ctx.exception.error_code, "anti_bot_blocked")

    def test_empty_page_stops_pagination(self) -> None:
        rate_limiter = MagicMock()
        with patch(
            "job_hunt.indeed_discovery.fetch",
            return_value=FetchResult(status=200, headers={}, body="<html><body></body></html>"),
        ) as mock_fetch:
            entries, truncated = discover_indeed_search(
                "https://www.indeed.com/jobs?q=x",
                rate_limiter,
                max_pages=5,
            )
        self.assertEqual(entries, [])
        self.assertFalse(truncated)
        # Only one fetch — no point paginating past an empty page.
        self.assertEqual(mock_fetch.call_count, 1)


class WatchlistIndeedEntryTest(unittest.TestCase):
    def test_indeed_entry_validates_and_emits(self) -> None:
        from job_hunt.watchlist import WatchlistEntry, parse_watchlist, watchlist_to_dict, Watchlist, WatchlistFilters

        entry = WatchlistEntry(
            name="IndeedSearch",
            indeed_search_url="https://www.indeed.com/jobs?q=python",
        )
        wl = Watchlist(companies=(entry,), filters=WatchlistFilters())
        d = watchlist_to_dict(wl)
        self.assertEqual(d["companies"][0]["indeed_search_url"], "https://www.indeed.com/jobs?q=python")
        # Round-trip through parse_watchlist (requires normalized dict with list of mappings)
        parsed = parse_watchlist(d)
        self.assertEqual(parsed.companies[0].indeed_search_url, "https://www.indeed.com/jobs?q=python")

    def test_indeed_url_must_target_indeed_com(self) -> None:
        from job_hunt.watchlist import WatchlistValidationError, parse_watchlist

        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({
                "companies": [
                    {"name": "X", "indeed_search_url": "https://example.com/jobs"}
                ]
            })

    def test_has_source_true_when_only_indeed(self) -> None:
        from job_hunt.watchlist import WatchlistEntry

        entry = WatchlistEntry(
            name="X",
            indeed_search_url="https://www.indeed.com/jobs?q=x",
        )
        self.assertTrue(entry.has_source())


if __name__ == "__main__":
    unittest.main()
