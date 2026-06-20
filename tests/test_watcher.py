"""Tests for the new-jobs watcher (src/job_hunt/watcher.py).

All fixtures are sanitized: placeholder companies/titles, no candidate PII.
The watcher must never apply, open a form, or generate a packet on its own; it
only classifies leads into a packet-readiness queue.
"""

from __future__ import annotations

import json
import sys
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


# Registry where only platform_backend is ready_local (mirrors the real repo).
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
        self.assertIn("location_work_mode_conflict", res["reasons"])

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


if __name__ == "__main__":
    unittest.main()
