"""Tests for the new-jobs watcher (src/job_hunt/watcher.py).

All fixtures are sanitized: placeholder companies/titles, no candidate PII.
The watcher must never apply, open a form, or generate a packet on its own; it
only classifies leads into a packet-readiness queue.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt import watcher

NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


# Controlled fixture: only platform_backend is ready_local here (generalist_swe
# is deliberately non-ready so reject/no-ready-lane paths can be exercised). The
# real repo registry also marks generalist_swe ready_local — see
# RealRegistryLaneTest for that.
REGISTRY = {
    "schema_version": 1,
    "default_variant": "generalist_swe",
    "variants": [
        {"id": "platform_backend", "review_status": "ready_local"},
        {"id": "ai_engineer"},
        {"id": "generalist_swe"},
    ],
}


def _lead(**overrides) -> dict:
    """A sanitized, strongly-fitting, in-window backend lead."""
    lead = {
        "lead_id": "acme-backend-engineer-0001",
        "fingerprint": "0001",
        "company": "Acme",
        "title": "Backend Engineer",
        "source": "greenhouse",
        "posting_url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": "Remote, US",
        "raw_description": "Build backend services in Python and Go.",
        "normalized_requirements": {"keywords": ["python", "go", "api"], "required": ["python"]},
        "ingested_at": _iso(30),
        "discovered_via": [{"discovered_at": _iso(30), "listing_updated_at": _iso(2)}],
        "observed_sources": [{"listing_updated_at": _iso(2)}],
        "fit_assessment": {
            "fit_score": 88,
            "fit_recommendation": "strong_yes",
            "missing_skills": [],
        },
    }
    lead.update(overrides)
    return lead


def _route(**overrides) -> dict:
    decision = {
        "selected_variant_id": "platform_backend",
        "selected_resume_exists": True,
        "needs_human_review": False,
        "confidence": "high",
        "review_reasons": [],
    }
    decision.update(overrides)
    return decision


# --------------------------------------------------------------------------- #
class ParseSinceHoursTest(unittest.TestCase):
    def test_accepts_ints_floats_strings(self) -> None:
        self.assertEqual(watcher.parse_since_hours(8), 8.0)
        self.assertEqual(watcher.parse_since_hours("12"), 12.0)
        self.assertEqual(watcher.parse_since_hours(1.5), 1.5)
        self.assertEqual(watcher.parse_since_hours("0.25"), 0.25)

    def test_rejects_zero(self) -> None:
        with self.assertRaises(watcher.WatcherError):
            watcher.parse_since_hours(0)

    def test_rejects_negative(self) -> None:
        with self.assertRaises(watcher.WatcherError):
            watcher.parse_since_hours(-3)
        with self.assertRaises(watcher.WatcherError):
            watcher.parse_since_hours("-1")

    def test_rejects_non_numeric(self) -> None:
        for bad in ("abc", "", None):
            with self.assertRaises(watcher.WatcherError):
                watcher.parse_since_hours(bad)

    def test_rejects_inf_nan(self) -> None:
        for bad in (float("inf"), float("nan")):
            with self.assertRaises(watcher.WatcherError):
                watcher.parse_since_hours(bad)


# --------------------------------------------------------------------------- #
class TimestampExtractionTest(unittest.TestCase):
    def test_posted_at_prefers_latest_listing_update(self) -> None:
        lead = _lead(
            observed_sources=[
                {"listing_updated_at": _iso(50)},
                {"listing_updated_at": _iso(2)},
                {"listing_updated_at": None},
            ],
            discovered_via=[{"discovered_at": _iso(30), "listing_updated_at": _iso(10)}],
        )
        posted = watcher.extract_posted_at(lead)
        self.assertEqual(watcher.parse_iso(posted), watcher.parse_iso(_iso(2)))

    def test_posted_at_none_when_no_listing_times(self) -> None:
        lead = _lead(observed_sources=[{"listing_updated_at": None}], discovered_via=[{"discovered_at": _iso(5)}])
        self.assertIsNone(watcher.extract_posted_at(lead))

    def test_discovered_at_is_earliest_first_seen(self) -> None:
        lead = _lead(
            ingested_at=_iso(20),
            discovered_via=[{"discovered_at": _iso(40)}, {"discovered_at": _iso(10)}],
        )
        got = watcher.extract_discovered_at(lead)
        self.assertEqual(watcher.parse_iso(got), watcher.parse_iso(_iso(40)))


# --------------------------------------------------------------------------- #
class FreshnessWindowTest(unittest.TestCase):
    def test_posted_at_preferred_over_discovered_at(self) -> None:
        # posted_at 2h ago, discovered_at 30h ago: basis must be posted_at/high.
        fr = watcher.compute_freshness(_lead(), now=NOW, since_hours=24)
        self.assertEqual(fr["freshness_basis"], "posted_at")
        self.assertEqual(fr["timestamp_confidence"], "high")
        self.assertTrue(fr["within_window"])

    def test_since_hours_1_excludes_2h_old_posting(self) -> None:
        fr = watcher.compute_freshness(_lead(), now=NOW, since_hours=1)
        self.assertEqual(fr["freshness_basis"], "posted_at")
        self.assertFalse(fr["within_window"])

    def test_since_hours_8_includes_2h_old_posting(self) -> None:
        fr = watcher.compute_freshness(_lead(), now=NOW, since_hours=8)
        self.assertTrue(fr["within_window"])

    def test_since_hours_12_boundary(self) -> None:
        lead = _lead(observed_sources=[{"listing_updated_at": _iso(10)}],
                     discovered_via=[{"discovered_at": _iso(40), "listing_updated_at": _iso(10)}])
        self.assertTrue(watcher.compute_freshness(lead, now=NOW, since_hours=12)["within_window"])
        self.assertFalse(watcher.compute_freshness(lead, now=NOW, since_hours=8)["within_window"])

    def test_fallback_to_discovered_at(self) -> None:
        lead = _lead(observed_sources=[{"listing_updated_at": None}],
                     discovered_via=[{"discovered_at": _iso(3), "listing_updated_at": None}],
                     ingested_at=_iso(3))
        fr = watcher.compute_freshness(lead, now=NOW, since_hours=8)
        self.assertEqual(fr["freshness_basis"], "discovered_at")
        self.assertEqual(fr["timestamp_confidence"], "fallback")
        self.assertTrue(fr["within_window"])

    def test_unknown_when_no_timestamps(self) -> None:
        lead = _lead(observed_sources=[], discovered_via=[], ingested_at=None)
        fr = watcher.compute_freshness(lead, now=NOW, since_hours=24)
        self.assertEqual(fr["freshness_basis"], "unknown")
        self.assertEqual(fr["timestamp_confidence"], "low")
        self.assertIsNone(fr["within_window"])


# --------------------------------------------------------------------------- #
class LaneAndTitleTest(unittest.TestCase):
    def test_lane_is_ready_only_for_ready_local(self) -> None:
        self.assertTrue(watcher.lane_is_ready(REGISTRY, "platform_backend"))
        self.assertFalse(watcher.lane_is_ready(REGISTRY, "generalist_swe"))
        self.assertFalse(watcher.lane_is_ready(REGISTRY, "ai_engineer"))
        self.assertFalse(watcher.lane_is_ready(REGISTRY, None))

    def test_senior_only_detection(self) -> None:
        for t in ("Staff Backend Engineer", "Principal Engineer", "Director of Engineering",
                  "VP Engineering", "Head of Platform", "Distinguished Engineer"):
            self.assertTrue(watcher.is_senior_only(t), t)

    def test_non_senior_titles_pass(self) -> None:
        for t in ("Backend Engineer", "Senior Software Engineer", "Software Engineer, Backend",
                  "Site Reliability Engineer", "Staffing Coordinator"):
            self.assertFalse(watcher.is_senior_only(t), t)


# --------------------------------------------------------------------------- #
class ClassifyReadinessTest(unittest.TestCase):
    def _freshness(self, **kw):
        base = {"freshness_basis": "posted_at", "timestamp_confidence": "high",
                "within_window": True, "posted_at": _iso(2), "discovered_at": _iso(30)}
        base.update(kw)
        return base

    def test_packet_ready_happy_path(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "packet_ready")
        self.assertTrue(res["recommend_packet"])
        self.assertFalse(res["requires_human_review"])

    def test_reject_when_lane_not_ready(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(selected_variant_id="generalist_swe"),
            freshness=self._freshness(), lane_ready=False, already_packeted=False,
        )
        self.assertEqual(res["status"], "reject")
        self.assertIn("no_ready_lane:generalist_swe", res["reasons"])

    def test_reject_when_already_packeted(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=True,
        )
        self.assertEqual(res["status"], "reject")
        self.assertEqual(res["reasons"], ["duplicate_existing_packet"])

    def test_reject_when_senior_only(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(title="Staff Backend Engineer"), route_decision=_route(),
            freshness=self._freshness(), lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "reject")
        self.assertIn("senior_staff_only", res["reasons"])

    def test_reject_when_out_of_window(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(),
            freshness=self._freshness(within_window=False), lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "reject")
        self.assertIn("outside_lookback_window:posted_at", res["reasons"])

    def test_reject_on_low_fit(self) -> None:
        lead = _lead(fit_assessment={"fit_score": 20, "fit_recommendation": "no", "missing_skills": []})
        res = watcher.classify_readiness(
            lead=lead, route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "reject")
        self.assertIn("low_fit_recommendation", res["reasons"])

    def test_reject_on_location_conflict_with_prefs(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(location="Mumbai, India"), route_decision=_route(),
            freshness=self._freshness(), lane_ready=True, already_packeted=False,
            prefs={"remote_only": True},
        )
        self.assertEqual(res["status"], "reject")
        self.assertIn("remote_only_pref_conflict", res["reasons"])

    def test_needs_review_on_fallback_freshness(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(),
            freshness=self._freshness(freshness_basis="discovered_at",
                                      timestamp_confidence="fallback", posted_at=None),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("freshness_fallback_discovered_at", res["reasons"])

    def test_needs_review_on_unknown_freshness(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(),
            freshness=self._freshness(freshness_basis="unknown",
                                      timestamp_confidence="low", within_window=None, posted_at=None),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("freshness_unknown", res["reasons"])

    def test_needs_review_on_low_route_confidence(self) -> None:
        res = watcher.classify_readiness(
            lead=_lead(), route_decision=_route(confidence="medium", needs_human_review=True,
                                                review_reasons=["near_tie_with:ai_engineer@40"]),
            freshness=self._freshness(), lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("route_confidence_medium", res["reasons"])

    def test_needs_review_on_skill_gaps(self) -> None:
        lead = _lead(fit_assessment={"fit_score": 80, "fit_recommendation": "strong_yes",
                                     "missing_skills": ["kubernetes"]})
        res = watcher.classify_readiness(
            lead=lead, route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "needs_review")
        self.assertTrue(any(r.startswith("skill_gaps:") for r in res["reasons"]))

    def test_needs_review_on_maybe_fit(self) -> None:
        lead = _lead(fit_assessment={"fit_score": 60, "fit_recommendation": "maybe", "missing_skills": []})
        res = watcher.classify_readiness(
            lead=lead, route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("fit_recommendation_maybe", res["reasons"])

    def test_needs_review_on_sparse_metadata(self) -> None:
        lead = _lead(raw_description="", normalized_requirements={})
        res = watcher.classify_readiness(
            lead=lead, route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=False,
        )
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("sparse_metadata", res["reasons"])


# --------------------------------------------------------------------------- #
class BuildQueueTest(unittest.TestCase):
    def _route_fn(self, lead, registry):
        return lead.get("_route", _route())

    def test_no_packet_generated_by_default(self) -> None:
        # build_queue must never apply or generate a packet — it only ranks.
        q = watcher.build_queue([_lead()], registry=REGISTRY, now=NOW, since_hours=24,
                                route_fn=self._route_fn)
        self.assertNotIn("packet", q)
        self.assertEqual(q["totals"]["packet_ready"], 1)

    def test_dedup_against_existing_packets(self) -> None:
        lead = _lead()
        q = watcher.build_queue([lead], registry=REGISTRY, now=NOW, since_hours=24,
                                packeted_lead_ids={lead["lead_id"]}, route_fn=self._route_fn)
        self.assertEqual(q["totals"]["reject"], 1)
        self.assertEqual(q["items"][0]["reasons"], ["duplicate_existing_packet"])

    def test_stale_dropped_by_default(self) -> None:
        # posted 2h ago, window 1h → stale → dropped.
        q = watcher.build_queue([_lead()], registry=REGISTRY, now=NOW, since_hours=1,
                                route_fn=self._route_fn)
        self.assertEqual(q["dropped_stale"], 1)
        self.assertEqual(len(q["items"]), 0)

    def test_stale_kept_when_include_stale(self) -> None:
        q = watcher.build_queue([_lead()], registry=REGISTRY, now=NOW, since_hours=1,
                                route_fn=self._route_fn, drop_stale=False)
        self.assertEqual(q["dropped_stale"], 0)
        self.assertEqual(q["items"][0]["status"], "reject")

    def test_ranking_puts_best_packet_ready_first(self) -> None:
        low = _lead(lead_id="low", fit_assessment={"fit_score": 76, "fit_recommendation": "strong_yes",
                                                   "missing_skills": []})
        high = _lead(lead_id="high", fit_assessment={"fit_score": 95, "fit_recommendation": "strong_yes",
                                                     "missing_skills": []})
        q = watcher.build_queue([low, high], registry=REGISTRY, now=NOW, since_hours=24,
                                route_fn=self._route_fn)
        ready = [it for it in q["items"] if it["status"] == "packet_ready"]
        self.assertEqual(ready[0]["lead_id"], "high")  # handler picks ready[0] as the single packet

    def test_max_candidates_caps_items(self) -> None:
        leads = [_lead(lead_id=f"acme-{i}", fingerprint=str(i)) for i in range(5)]
        q = watcher.build_queue(leads, registry=REGISTRY, now=NOW, since_hours=24,
                                max_candidates=2, route_fn=self._route_fn)
        self.assertEqual(len(q["items"]), 2)
        self.assertEqual(q["dropped_for_cap"], 3)

    def test_queue_item_carries_no_private_profile_content(self) -> None:
        # Inject a lead whose fit rationale contains PII-like content; the queue
        # item must NOT surface it — only public metadata + lane IDs + reasons.
        lead = _lead(fit_assessment={
            "fit_score": 90, "fit_recommendation": "strong_yes", "missing_skills": [],
            "fit_rationale": "candidate Kashane email private@example.com home address",
        })
        q = watcher.build_queue([lead], registry=REGISTRY, now=NOW, since_hours=24,
                                route_fn=self._route_fn)
        blob = json.dumps(q)
        self.assertNotIn("Kashane", blob)
        self.assertNotIn("@example.com", blob)
        self.assertNotIn("fit_rationale", blob)
        allowed = {
            "lead_id", "source_id", "company", "title", "source", "url",
            "discovered_at", "posted_at", "lookback_hours", "freshness_basis",
            "timestamp_confidence", "age_hours", "route_variant_id", "route_confidence",
            "selected_lane", "selected_resume_exists", "status", "score",
            "fit_recommendation", "reasons", "recommended_next_action",
            "recommend_packet", "requires_human_review",
        }
        self.assertEqual(set(q["items"][0].keys()), allowed)


# --------------------------------------------------------------------------- #
# Sanitized preferences fixtures (NEVER the real profile/raw/preferences.md).
# Mirrors the real file's frontmatter vocabulary with fake values.
_PREFS_MD = """---
document_type: preferences
title: Sanitized Prefs
candidate_name: Pat Placeholder
target_titles:
  - Backend Engineer
preferred_locations:
  - Remote
  - Springfield
  - Capital City
remote_preference: remote
excluded_keywords:
  - clearance
work_authorization: citizen
sponsorship_required: false
minimum_compensation: $150,000
---

Free-text notes that must be ignored by the parser.
"""

_PREFS_MD_REMOTE_ONLY = """---
remote_preference: remote_only
preferred_locations:
  - Springfield
minimum_compensation: 170000
sponsorship_required: false
---
"""


def _write_tmp(text: str) -> Path:
    fd = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    fd.write(text)
    fd.close()
    return Path(fd.name)


class ParseMoneyTest(unittest.TestCase):
    def test_parses_dollar_commas(self) -> None:
        self.assertEqual(watcher.parse_money("$150,000"), 150000.0)

    def test_parses_k_m_suffix(self) -> None:
        self.assertEqual(watcher.parse_money("180k"), 180000.0)
        self.assertEqual(watcher.parse_money("1.2M"), 1_200_000.0)

    def test_range_returns_upper_bound(self) -> None:
        self.assertEqual(watcher.parse_money("$150,000 - $200,000"), 200000.0)

    def test_ignores_small_bare_numbers(self) -> None:
        self.assertIsNone(watcher.parse_money("posted 5 days ago, 2026"))

    def test_none_for_empty(self) -> None:
        self.assertIsNone(watcher.parse_money(""))
        self.assertIsNone(watcher.parse_money(None))


class LoadPreferencesMdTest(unittest.TestCase):
    def test_parses_frontmatter_and_maps_keys(self) -> None:
        p = _write_tmp(_PREFS_MD)
        try:
            prefs = watcher.load_preferences_md(p)
        finally:
            p.unlink()
        self.assertEqual(prefs["remote_preferred"], True)
        self.assertEqual(prefs["remote_only"], False)  # "remote" != remote_only
        self.assertEqual(prefs["preferred_locations"], ["Remote", "Springfield", "Capital City"])
        self.assertEqual(prefs["compensation_floor"], 150000.0)
        self.assertEqual(prefs["requires_sponsorship"], False)

    def test_remote_only_value_sets_remote_only(self) -> None:
        p = _write_tmp(_PREFS_MD_REMOTE_ONLY)
        try:
            prefs = watcher.load_preferences_md(p)
        finally:
            p.unlink()
        self.assertTrue(prefs["remote_only"])
        self.assertTrue(prefs["remote_preferred"])

    def test_ignores_unknown_and_pii_keys(self) -> None:
        p = _write_tmp(_PREFS_MD)
        try:
            prefs = watcher.load_preferences_md(p)
        finally:
            p.unlink()
        for leaked in ("candidate_name", "title", "document_type", "target_titles",
                       "excluded_keywords", "minimum_compensation", "remote_preference"):
            self.assertNotIn(leaked, prefs)
        self.assertTrue(set(prefs).issubset(watcher._SAFE_NORMALIZED_KEYS))

    def test_missing_file_raises_watcher_error(self) -> None:
        with self.assertRaises(watcher.WatcherError):
            watcher.load_preferences_md(Path("/nonexistent/prefs-xyz.md"))

    def test_invalid_frontmatter_raises_watcher_error(self) -> None:
        # Malformed frontmatter raises WatcherError; the CLI catches it, warns,
        # and continues with no prefs (verified separately).
        p = _write_tmp("---\njust a scalar line without colon\n---\n")
        try:
            with self.assertRaises(watcher.WatcherError):
                watcher.load_preferences_md(p)
        finally:
            p.unlink()

    def test_frontmatterless_mapping_still_parses(self) -> None:
        # A plain key:value file (no --- fences) is parsed as a mapping.
        p = _write_tmp("remote_only: true\npreferred_locations:\n  - Remote\n")
        try:
            prefs = watcher.load_preferences_md(p)
        finally:
            p.unlink()
        self.assertTrue(prefs["remote_only"])

    def test_normalize_accepts_normalized_names_directly(self) -> None:
        prefs = watcher.normalize_preferences({
            "remote_only": True, "blocked_locations": ["India"],
            "compensation_floor": "200k", "current_location": "Springfield",
        })
        self.assertTrue(prefs["remote_only"])
        self.assertEqual(prefs["blocked_locations"], ["India"])
        self.assertEqual(prefs["compensation_floor"], 200000.0)
        self.assertEqual(prefs["current_location"], "Springfield")


class PreferenceGatingTest(unittest.TestCase):
    def _freshness(self):
        return {"freshness_basis": "posted_at", "timestamp_confidence": "high",
                "within_window": True, "posted_at": _iso(2), "discovered_at": _iso(30)}

    def _classify(self, lead, prefs):
        return watcher.classify_readiness(
            lead=lead, route_decision=_route(), freshness=self._freshness(),
            lane_ready=True, already_packeted=False, prefs=prefs,
        )

    def test_remote_only_rejects_onsite_out_of_area(self) -> None:
        res = self._classify(_lead(location="Gotham City"), {"remote_only": True})
        self.assertEqual(res["status"], "reject")
        self.assertIn("remote_only_pref_conflict", res["reasons"])

    def test_remote_preferred_downgrades_onsite_out_of_area(self) -> None:
        res = self._classify(_lead(location="Gotham City"),
                             {"remote_preferred": True, "preferred_locations": ["Springfield"]})
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("remote_pref_conflict", res["reasons"])

    def test_blocked_location_rejects(self) -> None:
        res = self._classify(_lead(location="Remote, India"),
                             {"blocked_locations": ["india"]})
        self.assertEqual(res["status"], "reject")
        self.assertIn("blocked_location", res["reasons"])

    def test_preferred_location_does_not_reject(self) -> None:
        res = self._classify(_lead(location="Springfield, US"),
                             {"remote_preferred": True, "preferred_locations": ["Springfield"]})
        self.assertEqual(res["status"], "packet_ready")

    def test_remote_location_satisfies_remote_only(self) -> None:
        res = self._classify(_lead(location="Remote, US"), {"remote_only": True})
        self.assertEqual(res["status"], "packet_ready")

    def test_ambiguous_location_needs_review_when_remote_matters(self) -> None:
        res = self._classify(_lead(location=""), {"remote_preferred": True})
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("location_ambiguous", res["reasons"])

    def test_compensation_below_floor_rejects_when_present(self) -> None:
        lead = _lead(compensation="$90,000 - $110,000")
        res = self._classify(lead, {"compensation_floor": 150000.0})
        self.assertEqual(res["status"], "reject")
        self.assertIn("compensation_below_floor", res["reasons"])

    def test_missing_compensation_does_not_reject(self) -> None:
        lead = _lead(compensation="")
        res = self._classify(lead, {"compensation_floor": 150000.0})
        self.assertEqual(res["status"], "packet_ready")

    def test_compensation_above_floor_ok(self) -> None:
        lead = _lead(compensation="$180,000 - $220,000")
        res = self._classify(lead, {"compensation_floor": 150000.0})
        self.assertEqual(res["status"], "packet_ready")

    def test_work_authorization_conflict_rejects_on_explicit_metadata(self) -> None:
        lead = _lead(raw_description="Great role. We do not offer sponsorship for this position.")
        res = self._classify(lead, {"requires_sponsorship": True})
        self.assertEqual(res["status"], "reject")
        self.assertIn("work_authorization_conflict", res["reasons"])

    def test_no_work_auth_conflict_when_not_required(self) -> None:
        lead = _lead(raw_description="We do not offer sponsorship for this position.")
        res = self._classify(lead, {"requires_sponsorship": False})
        self.assertEqual(res["status"], "packet_ready")


class PreferenceSummaryPrivacyTest(unittest.TestCase):
    def test_summary_is_booleans_and_counts_only(self) -> None:
        prefs = watcher.normalize_preferences({
            "remote_preference": "remote", "preferred_locations": ["Springfield", "Remote"],
            "minimum_compensation": "$150,000", "current_location": "Springfield",
            "work_authorization": "citizen",
        })
        summary = watcher.preferences_summary(prefs)
        blob = json.dumps(summary)
        # No raw values (locations, comp number, auth string) leak into the summary.
        self.assertNotIn("Springfield", blob)
        self.assertNotIn("150", blob)
        self.assertNotIn("citizen", blob)
        self.assertEqual(summary["preferred_locations_count"], 2)
        self.assertTrue(summary["compensation_floor_set"])
        self.assertFalse(summary["remote_only"])

    def test_queue_reasons_carry_codes_not_pref_text(self) -> None:
        prefs = {"blocked_locations": ["india"], "compensation_floor": 200000.0}
        lead = _lead(location="Remote, India", compensation="$90,000")
        q = watcher.build_queue([lead], registry=REGISTRY, now=NOW, since_hours=24,
                                prefs=prefs, route_fn=lambda l, r: _route())
        blob = json.dumps(q)
        self.assertNotIn("200000", blob)  # comp floor value never surfaced
        self.assertIn("blocked_location", blob)


class PacketCommandTest(unittest.TestCase):
    def test_basic_command(self) -> None:
        cmd = watcher.packet_command("acme-be-1", since_hours=8)
        self.assertEqual(
            cmd,
            "python3 scripts/job_hunt.py watch-new-jobs --since-hours 8 "
            "--lead-id acme-be-1 --emit-packet",
        )

    def test_includes_prefs_md_when_given(self) -> None:
        cmd = watcher.packet_command("x", since_hours=12.0, prefs_md="profile/raw/preferences.md")
        self.assertIn("--prefs-md profile/raw/preferences.md", cmd)
        self.assertIn("--since-hours 12", cmd)  # whole float renders as int
        self.assertIn("--lead-id x", cmd)
        self.assertIn("--emit-packet", cmd)


class ReviewSummaryTest(unittest.TestCase):
    def _build(self, leads, route_map, *, top=3, **kw):
        rf = lambda l, r: route_map[l["lead_id"]]
        q = watcher.build_queue(leads, registry=REGISTRY, now=NOW, since_hours=8, route_fn=rf, **kw)
        return watcher.finalize_queue(
            q, since_hours=8, prefs_md="profile/raw/preferences.md", top=top,
            source_mode="offline", queue_artifact="data/watch/x.json",
        )

    def _mixed(self, **kw):
        pr = _lead(lead_id="pr1", company="Acme", title="Backend Engineer")
        nr = _lead(lead_id="nr1", company="Beta", title="Platform Engineer",
                   fit_assessment={"fit_score": 60, "fit_recommendation": "maybe", "missing_skills": []})
        rj = _lead(lead_id="rj1", company="Gamma", title="Backend Engineer")
        routes = {"pr1": _route(), "nr1": _route(), "rj1": _route(selected_variant_id="generalist_swe")}
        return self._build([pr, nr, rj], routes, **kw)

    def test_summary_includes_packet_ready_top(self) -> None:
        rs = self._mixed()["review_summary"]
        self.assertEqual(len(rs["packet_ready"]), 1)
        self.assertEqual(rs["packet_ready"][0]["company"], "Acme")
        self.assertIn("packet_command", rs["packet_ready"][0])

    def test_summary_includes_needs_review_top(self) -> None:
        rs = self._mixed()["review_summary"]
        self.assertEqual(len(rs["needs_review"]), 1)
        self.assertEqual(rs["needs_review"][0]["company"], "Beta")
        # needs_review rows carry no packet command.
        self.assertNotIn("packet_command", rs["needs_review"][0])

    def test_summary_suppresses_individual_rejects(self) -> None:
        rs = self._mixed()["review_summary"]
        self.assertEqual(rs["reject"]["total"], 1)
        self.assertEqual(rs["reject"]["reason_counts"], {"no_ready_lane:generalist_swe": 1})
        # The summary's reject section is counts only — no per-lead rows.
        self.assertNotIn("items", rs["reject"])

    def test_packet_command_targets_best_lead(self) -> None:
        rs = self._mixed()["review_summary"]
        cmd = rs["packet_ready"][0]["packet_command"]
        self.assertIn("--lead-id pr1", cmd)
        self.assertIn("--emit-packet", cmd)
        self.assertTrue(cmd.startswith("python3 scripts/job_hunt.py watch-new-jobs"))

    def test_no_packet_command_when_no_packet_ready(self) -> None:
        rj = _lead(lead_id="rj1", company="Gamma")
        q = self._build([rj], {"rj1": _route(selected_variant_id="generalist_swe")})
        self.assertEqual(q["review_summary"]["packet_ready"], [])
        self.assertFalse(any("packet_command" in it for it in q["items"]))

    def test_top_limits_displayed_rows(self) -> None:
        leads = [_lead(lead_id=f"p{i}", company=f"Co{i}",
                       fit_assessment={"fit_score": 90 - i, "fit_recommendation": "strong_yes",
                                       "missing_skills": []})
                 for i in range(5)]
        routes = {f"p{i}": _route() for i in range(5)}
        q = self._build(leads, routes, top=2)
        self.assertEqual(len(q["review_summary"]["packet_ready"]), 2)
        # Underlying items are NOT truncated by --top.
        self.assertEqual(q["totals"]["packet_ready"], 5)

    def test_reason_counts_correct(self) -> None:
        a = _lead(lead_id="a")
        b = _lead(lead_id="b")
        c = _lead(lead_id="c")
        routes = {
            "a": _route(selected_variant_id="generalist_swe"),
            "b": _route(selected_variant_id="generalist_swe"),
            "c": _route(selected_variant_id="ai_engineer"),
        }
        q = self._build([a, b, c], routes)
        self.assertEqual(
            q["reason_counts"]["reject"],
            {"no_ready_lane:generalist_swe": 2, "no_ready_lane:ai_engineer": 1},
        )

    def test_items_carry_handoff_fields(self) -> None:
        q = self._mixed()
        for it in q["items"]:
            self.assertIn("rank", it)
            self.assertIn("primary_reason", it)
            self.assertIn("packet_recommended", it)
        pr = next(it for it in q["items"] if it["status"] == "packet_ready")
        self.assertTrue(pr["packet_recommended"])
        self.assertIn("packet_command", pr)
        self.assertEqual(pr["rank"], 1)
        self.assertIn("reason_counts", q)
        self.assertIn("review_summary", q)

    def test_rank_is_per_status(self) -> None:
        leads = [_lead(lead_id=f"p{i}",
                       fit_assessment={"fit_score": 90 - i, "fit_recommendation": "strong_yes",
                                       "missing_skills": []})
                 for i in range(3)]
        routes = {f"p{i}": _route() for i in range(3)}
        q = self._build(leads, routes)
        ranks = [it["rank"] for it in q["items"] if it["status"] == "packet_ready"]
        self.assertEqual(sorted(ranks), [1, 2, 3])

    def test_summary_has_no_private_content(self) -> None:
        # Inject PII-like rationale; the summary + handoff fields must not leak it.
        pr = _lead(lead_id="pr1", company="Acme", fit_assessment={
            "fit_score": 90, "fit_recommendation": "strong_yes", "missing_skills": [],
            "fit_rationale": "candidate Kashane private@example.com",
        })
        q = self._build([pr], {"pr1": _route()})
        blob = json.dumps(q)
        self.assertNotIn("Kashane", blob)
        self.assertNotIn("@example.com", blob)
        self.assertNotIn("fit_rationale", blob)


class ReasonGlossTest(unittest.TestCase):
    def test_exact_code(self) -> None:
        self.assertIn("local packet already exists",
                      watcher.reason_gloss("duplicate_existing_packet"))

    def test_suffixed_codes_strip_to_base(self) -> None:
        self.assertEqual(watcher.reason_gloss("no_ready_lane:generalist_swe"),
                         watcher.REASON_GLOSSARY["no_ready_lane"])
        self.assertEqual(watcher.reason_gloss("outside_lookback_window:posted_at"),
                         watcher.REASON_GLOSSARY["outside_lookback_window"])

    def test_route_confidence_variants(self) -> None:
        self.assertEqual(watcher.reason_gloss("route_confidence_medium"),
                         watcher.REASON_GLOSSARY["route_confidence"])

    def test_unknown_code_falls_back(self) -> None:
        self.assertIn("no description", watcher.reason_gloss("totally_made_up_code"))

    def test_every_emitted_base_has_a_gloss(self) -> None:
        # Guard against drift: each glossary entry resolves to a real string.
        for code in watcher.REASON_GLOSSARY:
            self.assertTrue(watcher.reason_gloss(code))


class ExplainTest(unittest.TestCase):
    def _explain(self, lead, route=None, **kw):
        rf = (lambda l, r: route) if route is not None else _route_fn_default
        return watcher.build_explanation(
            lead, registry=REGISTRY, now=NOW, since_hours=24,
            prefs_md="profile/raw/preferences.md", route_fn=rf, **kw,
        )

    def test_packet_ready_includes_packet_command(self) -> None:
        e = self._explain(_lead(lead_id="pr1"))
        self.assertEqual(e["readiness"]["status"], "packet_ready")
        cmd = e["next_action"]["packet_command"]
        self.assertIsNotNone(cmd)
        self.assertIn("--lead-id pr1", cmd)
        self.assertIn("--emit-packet", cmd)
        self.assertIsNone(e["next_action"]["no_command_reason"])

    def test_needs_review_has_glosses_and_no_command(self) -> None:
        lead = _lead(lead_id="nr1",
                     fit_assessment={"fit_score": 60, "fit_recommendation": "maybe", "missing_skills": []})
        e = self._explain(lead)
        self.assertEqual(e["readiness"]["status"], "needs_review")
        self.assertIsNone(e["next_action"]["packet_command"])
        self.assertIsNotNone(e["next_action"]["no_command_reason"])
        codes = [r["code"] for r in e["readiness"]["reasons"]]
        self.assertIn("fit_recommendation_maybe", codes)
        for r in e["readiness"]["reasons"]:
            self.assertTrue(r["gloss"])

    def test_reject_includes_primary_reason_and_glosses(self) -> None:
        e = self._explain(_lead(lead_id="rj1"), route=_route(selected_variant_id="generalist_swe"))
        self.assertEqual(e["readiness"]["status"], "reject")
        self.assertEqual(e["readiness"]["primary_reason"], "no_ready_lane:generalist_swe")
        self.assertEqual(e["readiness"]["reasons"][0]["gloss"],
                         watcher.REASON_GLOSSARY["no_ready_lane"])

    def test_includes_freshness_details(self) -> None:
        e = self._explain(_lead())
        fr = e["freshness"]
        self.assertEqual(fr["freshness_basis"], "posted_at")
        self.assertEqual(fr["timestamp_confidence"], "high")
        self.assertTrue(fr["within_window"])
        self.assertIsNotNone(fr["age_hours"])

    def test_includes_routing_details_with_alternatives(self) -> None:
        route = _route(score=72.5, confidence="high")
        route["alternatives"] = [
            {"variant_id": "ai_engineer", "score": 10.0, "resume_exists": False},
            {"variant_id": "generalist_swe", "score": 10.0, "resume_exists": True},
        ]
        e = self._explain(_lead(), route=route)
        rt = e["routing"]
        self.assertEqual(rt["selected_lane"], "platform_backend")
        self.assertEqual(rt["route_score"], 72.5)
        self.assertEqual(len(rt["alternatives"]), 2)
        self.assertEqual(rt["alternatives"][0]["lane_id"], "ai_engineer")

    def test_prefs_applied_has_no_raw_values(self) -> None:
        prefs = watcher.normalize_preferences({
            "remote_preference": "remote", "preferred_locations": ["Springfield", "Remote"],
            "minimum_compensation": "$150,000", "current_location": "Springfield",
            "work_authorization": "citizen",
        })
        e = self._explain(_lead(), prefs=prefs)
        blob = json.dumps(e["prefs_applied"])
        self.assertNotIn("Springfield", blob)
        self.assertNotIn("150", blob)
        self.assertNotIn("citizen", blob)
        self.assertEqual(e["prefs_applied"]["preferred_locations_count"], 2)

    def test_no_private_content_anywhere(self) -> None:
        lead = _lead(fit_assessment={
            "fit_score": 90, "fit_recommendation": "strong_yes", "missing_skills": [],
            "fit_rationale": "candidate Kashane private@example.com",
        })
        e = self._explain(lead)
        blob = json.dumps(e)
        self.assertNotIn("Kashane", blob)
        self.assertNotIn("@example.com", blob)
        self.assertNotIn("fit_rationale", blob)


class ExplainCliTest(unittest.TestCase):
    def test_lead_not_found_returns_error(self) -> None:
        import contextlib
        import io

        from job_hunt import core

        with tempfile.TemporaryDirectory() as leads_dir, \
                tempfile.TemporaryDirectory() as data_root:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = core.main([
                    "watch-new-jobs", "--explain", "no-such-lead",
                    "--leads-dir", leads_dir, "--data-root", data_root,
                    "--queue-dir", data_root,
                    "--registry", str(ROOT / "config" / "resume-variants.json"),
                ])
            out = buf.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("lead_not_found", out)

    def test_explain_does_not_generate_packet(self) -> None:
        # An empty data root means no packet dirs before OR after an explain run.
        import contextlib
        import io

        from job_hunt import core

        with tempfile.TemporaryDirectory() as leads_dir, \
                tempfile.TemporaryDirectory() as data_root:
            # Seed one sanitized lead file.
            (Path(leads_dir) / "acme.json").write_text(json.dumps(_lead(lead_id="acme1")))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = core.main([
                    "watch-new-jobs", "--explain", "acme1",
                    "--leads-dir", leads_dir, "--data-root", data_root,
                    "--queue-dir", data_root,
                    "--registry", str(ROOT / "config" / "resume-variants.json"),
                ])
            self.assertEqual(rc, 0)
            apps = Path(data_root) / "applications"
            self.assertFalse(apps.exists() and any(apps.iterdir()))


# Default route_fn for ExplainTest: a high-confidence platform_backend route.
def _route_fn_default(lead, registry):
    return _route()


# --------------------------------------------------------------------------- #
# Profiles + state + seen-suppression
# --------------------------------------------------------------------------- #
def _recent_lead(**kw) -> dict:
    """A sanitized lead stamped relative to the real clock so it stays in-window
    for CLI tests regardless of when they run."""
    now = datetime.now(timezone.utc)
    iso = (now - timedelta(hours=1)).isoformat()
    base = _lead(**kw)
    base["ingested_at"] = iso
    base["discovered_via"] = [{"discovered_at": iso, "listing_updated_at": iso}]
    base["observed_sources"] = [{"listing_updated_at": iso}]
    return base


class WatchProfilesTest(unittest.TestCase):
    def test_builtin_profiles(self) -> None:
        self.assertEqual(watcher.resolve_profile("hourly")["since_hours"], 1)
        self.assertEqual(watcher.resolve_profile("morning")["since_hours"], 12)
        self.assertEqual(watcher.resolve_profile("daily")["since_hours"], 24)
        cu = watcher.resolve_profile("catchup")
        self.assertEqual(cu["since_hours"], 72)
        self.assertEqual(cu["top"], 10)

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(watcher.WatcherError):
            watcher.resolve_profile("does-not-exist")

    def test_config_overrides_and_extends_builtins(self) -> None:
        p = _write_tmp("profiles:\n  - name: hourly\n    since_hours: 2\n    top: 9\n"
                       "  - name: custom\n    since_hours: 6\n")
        try:
            profs = watcher.load_watch_profiles(p)
        finally:
            p.unlink()
        self.assertEqual(profs["hourly"]["since_hours"], 2)
        self.assertEqual(profs["hourly"]["top"], 9)
        self.assertEqual(profs["custom"]["since_hours"], 6)
        self.assertEqual(profs["daily"]["since_hours"], 24)  # builtin survives

    def test_config_ignores_unknown_keys(self) -> None:
        p = _write_tmp("profiles:\n  - name: hourly\n    since_hours: 3\n    evil: bad\n")
        try:
            profs = watcher.load_watch_profiles(p)
        finally:
            p.unlink()
        self.assertEqual(profs["hourly"]["since_hours"], 3)
        self.assertNotIn("evil", profs["hourly"])

    def test_missing_config_returns_builtins(self) -> None:
        profs = watcher.load_watch_profiles(Path("/nonexistent/watch-profiles.yaml"))
        self.assertEqual(set(profs), set(watcher.BUILTIN_WATCH_PROFILES))


class StateRecordTest(unittest.TestCase):
    def _queue(self):
        return {
            "totals": {"packet_ready": 1, "needs_review": 1, "reject": 0},
            "items": [
                {"lead_id": "a", "status": "packet_ready"},
                {"lead_id": "b", "status": "needs_review"},
            ],
        }

    def test_record_has_non_private_fields(self) -> None:
        rec = watcher.build_state_record(
            "daily", last_run_at="2026-06-19T00:00:00+00:00", since_hours=24,
            queue=self._queue(), queue_artifact="data/watch/x.json", packet_lead_id="a",
        )
        self.assertEqual(rec["profile"], "daily")
        self.assertEqual(rec["since_hours"], 24)
        self.assertEqual(rec["seen_lead_ids"], ["a", "b"])
        self.assertEqual(rec["packet_lead_id"], "a")
        self.assertEqual(rec["counts"], {"packet_ready": 1, "needs_review": 1, "reject": 0})

    def test_record_has_no_private_content(self) -> None:
        # Even if items carried PII-ish keys, the record only takes lead_id/status.
        q = {"totals": {}, "items": [{"lead_id": "a", "status": "reject",
                                      "company": "Acme", "fit_rationale": "Kashane secret"}]}
        rec = watcher.build_state_record(
            "p", last_run_at="t", since_hours=1, queue=q, queue_artifact=None)
        blob = json.dumps(rec)
        self.assertNotIn("Kashane", blob)
        self.assertNotIn("fit_rationale", blob)
        self.assertNotIn("Acme", blob)

    def test_state_summary_compact(self) -> None:
        rec = watcher.build_state_record(
            "daily", last_run_at="t", since_hours=24, queue=self._queue(),
            queue_artifact=None, packet_lead_id=None)
        s = watcher.state_summary(rec)
        self.assertTrue(s["exists"])
        self.assertEqual(s["seen_lead_count"], 2)
        self.assertEqual(watcher.state_summary(None), {"exists": False})


class SeenSuppressionTest(unittest.TestCase):
    def _queue(self):
        return {
            "totals": {"packet_ready": 1, "needs_review": 0, "reject": 1},
            "items": [
                {"lead_id": "a", "status": "packet_ready", "reasons": ["fit_strong_lane_ready_in_window"]},
                {"lead_id": "b", "status": "reject", "reasons": ["no_ready_lane:x"]},
            ],
        }

    def test_marks_seen_without_changing_status(self) -> None:
        q = self._queue()
        n = watcher.apply_seen_suppression(q, {"a"})
        self.assertEqual(n, 1)
        a = q["items"][0]
        self.assertTrue(a["seen_before"])
        self.assertIn("seen_in_previous_watch", a["reasons"])
        self.assertEqual(a["status"], "packet_ready")  # status unchanged
        self.assertNotIn("hidden", a)

    def test_hide_flag_sets_hidden(self) -> None:
        q = self._queue()
        watcher.apply_seen_suppression(q, {"a"}, hide=True)
        self.assertTrue(q["items"][0]["hidden"])

    def test_empty_seen_is_noop(self) -> None:
        q = self._queue()
        self.assertEqual(watcher.apply_seen_suppression(q, set()), 0)

    def test_hidden_excluded_from_summary_but_counted(self) -> None:
        q = self._queue()
        watcher.apply_seen_suppression(q, {"a"}, hide=True)
        rs = watcher.build_review_summary(q, top=5)
        self.assertEqual(rs["packet_ready"], [])         # hidden from display
        self.assertEqual(rs["counts"]["packet_ready"], 1)  # still counted
        self.assertEqual(rs["hidden_seen"], 1)


def _seed(d: str, leads: list[dict]) -> None:
    for i, l in enumerate(leads):
        (Path(d) / f"lead{i}.json").write_text(json.dumps(l))


def _watch_argv(leads_dir: str, data_root: str, extra: list[str]) -> list[str]:
    return [
        "watch-new-jobs", "--leads-dir", leads_dir, "--data-root", data_root,
        "--queue-dir", str(Path(data_root) / "q"), "--state-dir", str(Path(data_root) / "state"),
        "--registry", str(ROOT / "config" / "resume-variants.json"),
    ] + extra


class WatchProfileStateCliTest(unittest.TestCase):
    def _run(self, leads_dir, data_root, extra):
        import contextlib
        import io

        from job_hunt import core
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = core.main(_watch_argv(leads_dir, data_root, extra))
        return rc, buf.getvalue()

    def test_profile_sets_since_hours(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            rc, out = self._run(ld, dr, ["--profile", "hourly", "--dry-run", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["review_summary"]["lookback_hours"], 1.0)

    def test_explicit_since_overrides_profile(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "8",
                                         "--dry-run", "--json"])
            self.assertEqual(json.loads(out)["review_summary"]["lookback_hours"], 8.0)

    def test_invalid_profile_errors(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            rc, out = self._run(ld, dr, ["--profile", "bogus", "--dry-run"])
            self.assertEqual(rc, 2)
            self.assertIn("unknown_profile", out)

    def test_since_hours_validation_with_profile(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            rc, out = self._run(ld, dr, ["--profile", "hourly", "--since-hours", "0", "--dry-run"])
            self.assertEqual(rc, 2)
            self.assertIn("invalid_since_hours", out)

    def test_no_state_written_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240"])
            self.assertFalse((Path(dr) / "state" / "daily.json").exists())

    def test_update_state_writes_non_private_fields(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a", company="Acme")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            sp = Path(dr) / "state" / "daily.json"
            self.assertTrue(sp.exists())
            rec = json.loads(sp.read_text())
            self.assertEqual(rec["profile"], "daily")
            self.assertIn("a", rec["seen_lead_ids"])
            self.assertNotIn("fit_rationale", sp.read_text())

    def test_show_state(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--show-state"])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["state"]["exists"])

    def test_reset_state(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            sp = Path(dr) / "state" / "daily.json"
            self.assertTrue(sp.exists())
            rc, out = self._run(ld, dr, ["--profile", "daily", "--reset-state"])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["removed"])
            self.assertFalse(sp.exists())

    def test_suppress_seen_marks_prior_leads(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--suppress-seen", "--json"])
            q = json.loads(out)
            self.assertEqual(q["seen_suppressed"], 1)
            self.assertTrue(any("seen_in_previous_watch" in (it.get("reasons") or [])
                                for it in q["items"]))

    def test_hide_seen_hides_from_summary(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--suppress-seen", "--hide-seen", "--json"])
            q = json.loads(out)
            self.assertEqual(q["review_summary"]["hidden_seen"], 1)
            self.assertEqual(q["review_summary"]["packet_ready"], [])


class ReviewReportTest(unittest.TestCase):
    def _finalized(self, leads, route_map, *, suppress_seen_ids=None, **kw):
        rf = lambda l, r: route_map[l["lead_id"]]
        q = watcher.build_queue(leads, registry=REGISTRY, now=NOW, since_hours=24,
                                route_fn=rf, **kw)
        if suppress_seen_ids:
            watcher.apply_seen_suppression(q, set(suppress_seen_ids))
        q["leads_considered"] = len(leads)
        q["already_packeted"] = 0
        q["prefs_applied"] = watcher.preferences_summary(None)
        watcher.finalize_queue(q, since_hours=24, prefs_md="profile/raw/preferences.md",
                               top=3, source_mode="offline", queue_artifact="data/watch/x.json")
        return q

    def _report(self, q, **kw):
        kw.setdefault("profile", "daily")
        kw.setdefault("prefs_md", "profile/raw/preferences.md")
        kw.setdefault("since_hours", 24)
        return watcher.build_review_report(q, **kw)

    def test_header_metadata(self) -> None:
        q = self._finalized([_lead(lead_id="pr1")], {"pr1": _route()})
        r = self._report(q, state_path="data/watch/state/daily.json", state_written="x")
        h = r["header"]
        self.assertEqual(h["profile"], "daily")
        self.assertEqual(h["lookback_hours"], 24)
        self.assertEqual(h["source_mode"], "offline")
        self.assertEqual(h["queue_artifact"], "data/watch/x.json")
        self.assertEqual(h["state_path"], "data/watch/state/daily.json")
        self.assertTrue(h["state_written"])

    def test_packet_ready_top_action_has_command(self) -> None:
        q = self._finalized([_lead(lead_id="pr1", company="Acme")], {"pr1": _route()})
        r = self._report(q)
        self.assertEqual(r["top_action"]["kind"], "generate_packet")
        self.assertIn("--emit-packet", r["top_action"]["command"])
        self.assertIn("--lead-id pr1", r["top_action"]["command"])

    def test_needs_review_top_action_has_explain_command(self) -> None:
        nr = _lead(lead_id="nr1", company="Beta",
                   fit_assessment={"fit_score": 60, "fit_recommendation": "maybe", "missing_skills": []})
        q = self._finalized([nr], {"nr1": _route()})
        r = self._report(q)
        self.assertEqual(r["top_action"]["kind"], "review")
        self.assertIn("--explain nr1", r["top_action"]["command"])

    def test_rejects_only_suggests_widen_or_discover(self) -> None:
        rj = _lead(lead_id="rj1")
        q = self._finalized([rj], {"rj1": _route(selected_variant_id="generalist_swe")})
        r = self._report(q)
        self.assertEqual(r["top_action"]["kind"], "widen_or_discover")
        self.assertTrue(r["top_action"]["command"])
        msg = r["top_action"]["message"].lower()
        self.assertTrue("lane" in msg or "lookback" in msg or "discovery" in msg)

    def test_seen_suppression_reflected(self) -> None:
        pr = _lead(lead_id="pr1")
        q = self._finalized([pr], {"pr1": _route()}, suppress_seen_ids=["pr1"])
        q["seen_suppressed"] = 1
        r = self._report(q, suppress_seen=True)
        self.assertTrue(r["delta"]["suppress_seen_active"])
        self.assertEqual(r["delta"]["new_leads"], 0)  # pr1 was seen before

    def test_decision_top_reject_reasons(self) -> None:
        leads = [_lead(lead_id="a"), _lead(lead_id="b"), _lead(lead_id="c")]
        routes = {"a": _route(selected_variant_id="generalist_swe"),
                  "b": _route(selected_variant_id="generalist_swe"),
                  "c": _route(selected_variant_id="ai_engineer")}
        q = self._finalized(leads, routes)
        r = self._report(q)
        self.assertEqual(r["decision"]["reject"], 3)
        top = {x["code"]: x["count"] for x in r["decision"]["top_reject_reasons"]}
        self.assertEqual(top["no_ready_lane:generalist_swe"], 2)

    def test_safety_footer_present(self) -> None:
        q = self._finalized([_lead(lead_id="pr1")], {"pr1": _route()})
        r = self._report(q)
        self.assertEqual(len(r["safety"]), 3)
        joined = " ".join(r["safety"]).lower()
        self.assertIn("human submit", joined)
        self.assertIn("no apply", joined)

    def test_commands_are_valid_and_present(self) -> None:
        q = self._finalized([_lead(lead_id="pr1")], {"pr1": _route()})
        r = self._report(q)
        c = r["commands"]
        self.assertTrue(c["rerun_discovery"].endswith("--discover"))
        self.assertIn("--profile catchup", c["wider_lookback"])
        for cmd in c.values():
            if cmd:
                self.assertTrue(cmd.startswith("python3 scripts/job_hunt.py watch-new-jobs"))

    def test_report_has_no_private_content(self) -> None:
        pr = _lead(lead_id="pr1", company="Acme", fit_assessment={
            "fit_score": 90, "fit_recommendation": "strong_yes", "missing_skills": [],
            "fit_rationale": "candidate Kashane private@example.com",
        })
        q = self._finalized([pr], {"pr1": _route()})
        r = self._report(q)
        blob = json.dumps(r)
        self.assertNotIn("Kashane", blob)
        self.assertNotIn("@example.com", blob)
        self.assertNotIn("fit_rationale", blob)

    def test_state_next_command(self) -> None:
        cmd = watcher.state_next_command(None, "morning", prefs_md="profile/raw/preferences.md")
        self.assertIn("--profile morning", cmd)
        self.assertIn("--prefs-md profile/raw/preferences.md", cmd)
        self.assertIn("--update-state", cmd)


class ReviewReportCliTest(unittest.TestCase):
    def _run(self, leads_dir, data_root, extra):
        import contextlib
        import io

        from job_hunt import core
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = core.main(_watch_argv(leads_dir, data_root, extra))
        return rc, buf.getvalue()

    def test_review_report_json_includes_report(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--review-report", "--json"])
            self.assertEqual(rc, 0)
            q = json.loads(out)
            self.assertIn("review_report", q)
            self.assertIn("safety", q["review_report"])

    def test_review_report_text_has_sections(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--review-report"])
            self.assertEqual(rc, 0)
            for marker in ("review report", "what changed", "decision", "top action", "safety"):
                self.assertIn(marker, out.lower())

    def test_state_update_reflected_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--update-state", "--review-report", "--json"])
            q = json.loads(out)
            self.assertTrue(q["review_report"]["header"]["state_written"])

    def test_no_state_written_without_flag_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--review-report", "--json"])
            q = json.loads(out)
            self.assertFalse(q["review_report"]["header"]["state_written"])
            self.assertFalse((Path(dr) / "state" / "daily.json").exists())

    def test_show_state_has_next_command(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--show-state"])
            self.assertEqual(rc, 0)
            d = json.loads(out)
            self.assertIn("next_command", d)
            self.assertIn("--profile daily", d["next_command"])


class RunDeltaTest(unittest.TestCase):
    def _queue(self, lead_ids):
        return {"items": [{"lead_id": i, "status": "reject"} for i in lead_ids]}

    def _prior(self, lead_ids, last_run_at="2026-06-18T00:00:00+00:00"):
        return {"seen_lead_ids": list(lead_ids), "last_run_at": last_run_at}

    def test_first_run_no_prior_state(self) -> None:
        d = watcher.compute_run_delta(None, self._queue(["a", "b"]))
        self.assertFalse(d["has_prior_state"])
        self.assertIsNone(d["new_since_last_run"])
        self.assertIsNone(d["resolved_since_last_run"])
        self.assertEqual(d["new_lead_ids"], [])

    def test_all_new(self) -> None:
        d = watcher.compute_run_delta(self._prior([]), self._queue(["a", "b"]))
        self.assertTrue(d["has_prior_state"])
        self.assertEqual(d["new_since_last_run"], 2)
        self.assertEqual(d["seen_again_since_last_run"], 0)
        self.assertEqual(d["resolved_since_last_run"], 0)
        self.assertEqual(set(d["new_lead_ids"]), {"a", "b"})

    def test_partial_overlap(self) -> None:
        # prior: a,b,c ; now: b,c,d -> new=d, seen_again=b,c, resolved=a
        d = watcher.compute_run_delta(self._prior(["a", "b", "c"]), self._queue(["b", "c", "d"]))
        self.assertEqual(d["new_since_last_run"], 1)
        self.assertEqual(d["seen_again_since_last_run"], 2)
        self.assertEqual(d["resolved_since_last_run"], 1)
        self.assertEqual(d["new_lead_ids"], ["d"])
        self.assertEqual(d["resolved_lead_ids"], ["a"])

    def test_all_seen_again(self) -> None:
        d = watcher.compute_run_delta(self._prior(["a", "b"]), self._queue(["a", "b"]))
        self.assertEqual(d["new_since_last_run"], 0)
        self.assertEqual(d["seen_again_since_last_run"], 2)
        self.assertEqual(d["resolved_since_last_run"], 0)

    def test_resolved_when_prior_absent_now(self) -> None:
        d = watcher.compute_run_delta(self._prior(["a", "b", "c"]), self._queue(["a"]))
        self.assertEqual(d["resolved_since_last_run"], 2)
        self.assertEqual(d["resolved_lead_ids"], ["b", "c"])

    def test_prior_last_run_at_surfaced(self) -> None:
        d = watcher.compute_run_delta(self._prior(["a"], last_run_at="2026-06-19T09:00:00+00:00"),
                                      self._queue(["a"]))
        self.assertEqual(d["prior_last_run_at"], "2026-06-19T09:00:00+00:00")

    def test_id_lists_capped(self) -> None:
        many = [f"lead-{i}" for i in range(50)]
        d = watcher.compute_run_delta(self._prior([]), self._queue(many))
        self.assertEqual(d["new_since_last_run"], 50)        # count is full
        self.assertEqual(len(d["new_lead_ids"]), 20)         # id list is capped

    def test_report_includes_run_delta(self) -> None:
        q = {"items": [{"lead_id": "a", "status": "reject", "reasons": ["no_ready_lane:x"]}],
             "review_summary": {"counts": {"packet_ready": 0, "needs_review": 0, "reject": 1},
                                "reject": {"reason_counts": {"no_ready_lane:x": 1}},
                                "packet_ready": [], "needs_review": []}}
        r = watcher.build_review_report(q, profile="daily", prior_state=self._prior(["z"]))
        self.assertTrue(r["run_delta"]["has_prior_state"])
        self.assertEqual(r["run_delta"]["new_since_last_run"], 1)   # 'a' is new vs prior {'z'}
        self.assertEqual(r["run_delta"]["resolved_since_last_run"], 1)  # 'z' cleared

    def test_no_private_content_in_delta(self) -> None:
        prior = self._prior(["acme-backend-1"])
        prior["candidate_name"] = "Kashane"  # should never be read
        q = {"items": [{"lead_id": "beta-platform-2", "status": "reject"}]}
        d = watcher.compute_run_delta(prior, q)
        self.assertNotIn("Kashane", json.dumps(d))


class RunDeltaCliTest(unittest.TestCase):
    def _run(self, leads_dir, data_root, extra):
        import contextlib
        import io

        from job_hunt import core
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = core.main(_watch_argv(leads_dir, data_root, extra))
        return rc, buf.getvalue()

    def test_first_run_reports_no_prior_state(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--review-report"])
            self.assertIn("no prior daily state found", out)

    def test_second_run_shows_delta_without_suppress_seen(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            # Second run: no --suppress-seen, but delta still computed.
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--review-report", "--json"])
            q = json.loads(out)
            rd = q["run_delta"]
            self.assertTrue(rd["has_prior_state"])
            self.assertEqual(rd["seen_again_since_last_run"], 1)
            self.assertEqual(rd["new_since_last_run"], 0)

    def test_delta_computed_before_state_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            # Add a new lead, then rerun WITH --update-state: delta must reflect
            # the prior (single-lead) state, not the about-to-be-written one.
            _seed(ld, [_recent_lead(lead_id="a"), _recent_lead(lead_id="b", company="Beta")])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--update-state", "--json"])
            rd = json.loads(out)["run_delta"]
            self.assertEqual(rd["new_since_last_run"], 1)   # 'b' is new
            self.assertEqual(rd["seen_again_since_last_run"], 1)  # 'a' seen again

    def test_delta_works_with_suppress_seen(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--since-hours", "240",
                                         "--suppress-seen", "--json"])
            q = json.loads(out)
            self.assertTrue(q["run_delta"]["has_prior_state"])
            self.assertEqual(q["run_delta"]["seen_again_since_last_run"], 1)

    def test_show_state_has_delta_command(self) -> None:
        with tempfile.TemporaryDirectory() as ld, tempfile.TemporaryDirectory() as dr:
            _seed(ld, [_recent_lead(lead_id="a")])
            self._run(ld, dr, ["--profile", "daily", "--since-hours", "240", "--update-state"])
            rc, out = self._run(ld, dr, ["--profile", "daily", "--show-state"])
            d = json.loads(out)
            self.assertIn("delta_command", d)
            self.assertIn("--review-report", d["delta_command"])


class RealRegistryLaneTest(unittest.TestCase):
    """The real repo registry must mark generalist_swe ready_local so the watcher
    stops rejecting generalist leads as no_ready_lane (metadata only — no private
    resume content read here)."""

    def test_generalist_swe_recognized_ready(self) -> None:
        from job_hunt.resume_registry import load_registry
        reg = load_registry(ROOT / "config" / "resume-variants.json")
        self.assertTrue(watcher.lane_is_ready(reg, "generalist_swe"))
        self.assertTrue(watcher.lane_is_ready(reg, "platform_backend"))
        self.assertTrue(watcher.lane_is_ready(reg, "fullstack_product"))
        # ai_engineer resume is not authored yet -> not a ready lane.
        self.assertFalse(watcher.lane_is_ready(reg, "ai_engineer"))


if __name__ == "__main__":
    unittest.main()
