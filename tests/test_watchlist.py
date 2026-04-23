from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.watchlist import (
    Watchlist,
    WatchlistEntry,
    WatchlistFilters,
    WatchlistValidationError,
    load_watchlist,
    parse_watchlist,
    validate_cli_string,
    watchlist_add,
    watchlist_remove,
    watchlist_show,
    watchlist_validate,
    write_watchlist,
)


VALID_YAML = """
companies:
  - name: "ExampleCo"
    greenhouse: "exampleco"
    ashby: "exampleco"
  - name: "AnotherCorp"
    lever: "anothercorp"
    workable: "anothercorp"
  - name: "Federal"
    usajobs_search_profile: "federal_remote"

usajobs_profiles:
  - name: "federal_remote"
    keyword: "platform engineer"
    location_name: "Washington, District of Columbia"
    results_per_page: 25
    who_may_apply: "Public"
    remote_indicator: true
    fields: "Full"

filters:
  keywords_any:
    - "engineer"
  keywords_none:
    - "clearance required"
"""


class ParseWatchlistTest(unittest.TestCase):
    def test_valid_yaml_parses(self) -> None:
        from job_hunt.simple_yaml import loads as load_yaml
        data = load_yaml(VALID_YAML)
        wl = parse_watchlist(data)
        self.assertEqual(len(wl.companies), 3)
        self.assertEqual(wl.companies[0].name, "ExampleCo")
        self.assertEqual(wl.companies[0].greenhouse, "exampleco")
        self.assertEqual(wl.companies[0].ashby, "exampleco")
        self.assertEqual(wl.filters.keywords_any, ("engineer",))
        self.assertEqual(wl.companies[2].usajobs_profile.name, "federal_remote")

    def test_missing_name_rejected(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({"companies": [{"greenhouse": "x"}]})

    def test_name_path_traversal_rejected(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({"companies": [{"name": "../../etc/passwd"}]})

    def test_http_careers_url_rejected(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({"companies": [
                {"name": "CoX", "careers_url": "http://ex.com/careers"},
            ]})

    def test_too_many_companies_rejected(self) -> None:
        entries = [{"name": f"Co{i:03d}"} for i in range(201)]
        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({"companies": entries})

    def test_duplicate_name_rejected(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({"companies": [
                {"name": "X"}, {"name": "X"},
            ]})

    def test_invalid_usajobs_profile_rejected(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            parse_watchlist({
                "companies": [{"name": "Federal", "usajobs_search_profile": "federal"}],
                "usajobs_profiles": [{"name": "federal", "results_per_page": 999}],
            })


class FilterSemanticsTest(unittest.TestCase):
    def test_keywords_none_wins(self) -> None:
        f = WatchlistFilters(
            keywords_any=("engineer",),
            keywords_none=("clearance",),
        )
        ok, reason = f.passes("Senior Engineer (Clearance required)", "Remote")
        self.assertFalse(ok)
        self.assertIn("keywords_none", reason)

    def test_keywords_any_missing(self) -> None:
        f = WatchlistFilters(keywords_any=("engineer",))
        ok, _ = f.passes("Product Manager", "Remote")
        self.assertFalse(ok)

    def test_location_required(self) -> None:
        f = WatchlistFilters(locations_any=("remote", "san diego"))
        self.assertTrue(f.passes("Engineer", "Remote - US")[0])
        self.assertFalse(f.passes("Engineer", "Paris")[0])

    def test_seniority_title_only(self) -> None:
        f = WatchlistFilters(seniority_any=("senior", "staff"))
        self.assertTrue(f.passes("Senior Engineer", "Remote")[0])
        self.assertFalse(f.passes("Engineer", "Remote (senior team)")[0])

    def test_empty_lists_are_noop(self) -> None:
        f = WatchlistFilters()
        self.assertTrue(f.passes("anything", "anywhere")[0])


class ValidateCliStringTest(unittest.TestCase):
    def test_accepts_plain_string(self) -> None:
        self.assertEqual(validate_cli_string("Hello World", "name"), "Hello World")

    def test_rejects_control_chars(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            validate_cli_string("bad\x00value", "notes")
        with self.assertRaises(WatchlistValidationError):
            validate_cli_string("bad\nvalue", "notes")


class WatchlistCrudTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "watchlist.yaml"

    def test_add_to_empty(self) -> None:
        wl = watchlist_add(self.path, {"name": "NewCo", "greenhouse": "newco"})
        self.assertEqual(len(wl.companies), 1)
        reloaded = load_watchlist(self.path)
        self.assertEqual(reloaded.companies[0].name, "NewCo")

    def test_add_duplicate_rejected(self) -> None:
        watchlist_add(self.path, {"name": "NewCo", "greenhouse": "newco"})
        with self.assertRaises(WatchlistValidationError) as ctx:
            watchlist_add(self.path, {"name": "NewCo", "greenhouse": "dup"})
        self.assertIn("watchlist_entry_exists", str(ctx.exception))

    def test_remove_not_found_raises(self) -> None:
        watchlist_add(self.path, {"name": "NewCo", "greenhouse": "newco"})
        with self.assertRaises(WatchlistValidationError):
            watchlist_remove(self.path, "NotHere")

    def test_show_all_and_single(self) -> None:
        watchlist_add(self.path, {"name": "AA", "greenhouse": "a"})
        watchlist_add(self.path, {"name": "BB", "lever": "b", "workable": "bb"})
        all_data = watchlist_show(self.path)
        self.assertEqual(len(all_data["companies"]), 2)
        one = watchlist_show(self.path, company="AA")
        self.assertEqual(one["companies"][0]["name"], "AA")

    def test_add_warns_on_comment_loss(self) -> None:
        self.path.write_text("# header comment\ncompanies:\n  - name: \"X\"\n", encoding="utf-8")
        with self.assertRaises(WatchlistValidationError) as ctx:
            watchlist_add(self.path, {"name": "Y", "greenhouse": "y"})
        self.assertIn("watchlist_comments_present", str(ctx.exception))

    def test_add_force_overrides_comment_warning(self) -> None:
        self.path.write_text("# header\ncompanies:\n  - name: \"X\"\n", encoding="utf-8")
        wl = watchlist_add(self.path, {"name": "Y", "greenhouse": "y"}, force=True)
        self.assertEqual([c.name for c in wl.companies], ["X", "Y"])


class WatchlistValidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "watchlist.yaml"

    def test_valid_file_ok(self) -> None:
        self.path.write_text(VALID_YAML, encoding="utf-8")
        result = watchlist_validate(self.path)
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_missing_source_warns(self) -> None:
        self.path.write_text("companies:\n  - name: \"NoSource\"\n", encoding="utf-8")
        result = watchlist_validate(self.path)
        self.assertTrue(result["valid"])
        self.assertTrue(any("NoSource" in w for w in result["warnings"]))

    def test_usajobs_profile_missing_is_reported_in_readiness(self) -> None:
        self.path.write_text(
            """
companies:
  - name: "Federal"
    usajobs_search_profile: "federal"
""",
            encoding="utf-8",
        )
        result = watchlist_validate(self.path)
        self.assertTrue(result["valid"])
        self.assertEqual(result["source_readiness"][0]["state"], "profile_missing")

    def test_invalid_file_errors(self) -> None:
        self.path.write_text("companies:\n  - greenhouse: \"x\"\n", encoding="utf-8")
        result = watchlist_validate(self.path)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
