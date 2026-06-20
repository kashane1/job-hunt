"""New-jobs watcher: freshness windowing + packet-readiness queue.

A browserless, no-apply, human-gated reader over already-discovered leads. It
classifies each lead into one of three readiness statuses — ``packet_ready``,
``needs_review``, ``reject`` — using conservative first-pass rules, then emits a
ranked local queue. It NEVER applies, opens a form, or submits anything; a
``packet_ready`` lead still requires the human-submit invariant downstream.

This module is intentionally pure/stdlib-only and does not import ``core`` so it
stays cheap to unit-test. The CLI handler in ``core.py`` wires discovery,
scoring, and packet generation around these functions.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .resume_registry import route_lead
from .simple_yaml import loads as _yaml_loads

# --- vocabularies (kept in sync with tests + schema-free queue artifact) ---
READINESS_STATUSES: tuple[str, ...] = ("packet_ready", "needs_review", "reject")
FRESHNESS_BASES: tuple[str, ...] = ("posted_at", "discovered_at", "unknown")
TIMESTAMP_CONFIDENCE: tuple[str, ...] = ("high", "fallback", "low")

# Sort priority: packet_ready first, reject last.
_STATUS_RANK: dict[str, int] = {"packet_ready": 0, "needs_review": 1, "reject": 2}

# Titles that are clearly senior/staff-only and out of scope for the
# first-pass platform_backend lane. Matched as whole words / phrases on the
# lowercased title so "staffing" or "leadership" don't trip "staff"/"lead".
_SENIOR_ONLY_MARKERS: tuple[str, ...] = (
    "staff",
    "principal",
    "distinguished",
    "fellow",
    "director",
    "vp",
    "vice president",
    "head of",
    "chief",
)


class WatcherError(ValueError):
    """Raised for invalid watcher input (e.g. a non-positive lookback)."""


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #
def parse_since_hours(raw: object) -> float:
    """Coerce a ``--since-hours`` value to a positive float.

    Accepts ints, floats, or numeric strings. Rejects zero, negatives, and
    non-numeric input with :class:`WatcherError` so the CLI can surface a clear
    validation message.
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise WatcherError(f"--since-hours must be a positive number, got {raw!r}")
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        raise WatcherError(f"--since-hours must be a finite positive number, got {raw!r}")
    if value <= 0:
        raise WatcherError(f"--since-hours must be > 0, got {value}")
    return value


def parse_iso(ts: object) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware UTC datetime, or None."""
    if not isinstance(ts, str) or not ts.strip():
        return None
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Timestamp extraction
# --------------------------------------------------------------------------- #
def _iter_listing_timestamps(lead: dict):
    for key in ("observed_sources", "discovered_via"):
        for rec in lead.get(key) or []:
            if isinstance(rec, dict):
                yield rec.get("listing_updated_at")


def extract_posted_at(lead: dict) -> str | None:
    """Best source-provided posting timestamp (latest ``listing_updated_at``).

    Returns the most recent non-null listing-update time across the lead's
    provenance records, or None when no provider exposed one.
    """
    best: datetime | None = None
    best_raw: str | None = None
    for raw in _iter_listing_timestamps(lead):
        dt = parse_iso(raw)
        if dt is None:
            continue
        if best is None or dt > best:
            best, best_raw = dt, raw
    return best_raw


def extract_discovered_at(lead: dict) -> str | None:
    """Earliest first-seen time for the lead (discovery-time fallback)."""
    candidates: list[tuple[datetime, str]] = []
    ingested = lead.get("ingested_at")
    dt = parse_iso(ingested)
    if dt is not None and isinstance(ingested, str):
        candidates.append((dt, ingested))
    for rec in lead.get("discovered_via") or []:
        if isinstance(rec, dict):
            raw = rec.get("discovered_at")
            d = parse_iso(raw)
            if d is not None and isinstance(raw, str):
                candidates.append((d, raw))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


# --------------------------------------------------------------------------- #
# Freshness
# --------------------------------------------------------------------------- #
def compute_freshness(lead: dict, *, now: datetime, since_hours: float) -> dict:
    """Decide whether a lead falls inside the lookback window.

    Prefers a real posting timestamp (``posted_at`` → confidence ``high``);
    falls back to discovery time (``discovered_at`` → confidence ``fallback``);
    when neither exists the basis is ``unknown`` (confidence ``low``) and
    ``within_window`` is None — the caller must not claim it is "posted in the
    last X hours".
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    posted_at = extract_posted_at(lead)
    discovered_at = extract_discovered_at(lead)

    def _age_hours(raw: str | None) -> float | None:
        dt = parse_iso(raw)
        if dt is None:
            return None
        return (now - dt).total_seconds() / 3600.0

    if posted_at is not None:
        age = _age_hours(posted_at)
        return {
            "freshness_basis": "posted_at",
            "timestamp_confidence": "high",
            "posted_at": posted_at,
            "discovered_at": discovered_at,
            "age_hours": age,
            "within_window": (age is not None and age <= since_hours),
        }
    if discovered_at is not None:
        age = _age_hours(discovered_at)
        return {
            "freshness_basis": "discovered_at",
            "timestamp_confidence": "fallback",
            "posted_at": None,
            "discovered_at": discovered_at,
            "age_hours": age,
            "within_window": (age is not None and age <= since_hours),
        }
    return {
        "freshness_basis": "unknown",
        "timestamp_confidence": "low",
        "posted_at": None,
        "discovered_at": None,
        "age_hours": None,
        "within_window": None,
    }


# --------------------------------------------------------------------------- #
# Lane / title helpers
# --------------------------------------------------------------------------- #
def lane_is_ready(registry: dict, variant_id: str | None) -> bool:
    """True when the registry marks ``variant_id`` review_status=ready_local."""
    if not variant_id:
        return False
    for variant in registry.get("variants", []) or []:
        if variant.get("id") == variant_id:
            return variant.get("review_status") == "ready_local"
    return False


def _word_in(text: str, phrase: str) -> bool:
    """Whole-word / phrase containment on a lowercased string."""
    import re

    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


def is_senior_only(title: str) -> bool:
    title_lc = (title or "").lower()
    return any(_word_in(title_lc, m) for m in _SENIOR_ONLY_MARKERS)


_MONEY = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?")


def parse_money(text: object) -> float | None:
    """Best-effort extraction of the largest money figure from a string.

    Returns the max numeric value found (so a salary range yields its upper
    bound, used for "clearly below floor" comparisons). Bare numbers under 1000
    with no k/m suffix are ignored as noise (years, counts). None when nothing
    parseable is found.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    best: float | None = None
    for num, suffix in _MONEY.findall(text):
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        if suffix in ("k", "K"):
            value *= 1_000
        elif suffix in ("m", "M"):
            value *= 1_000_000
        elif value < 10000:
            continue  # bare small number (count, year) — not a salary figure
        if best is None or value > best:
            best = value
    return best


def _preference_signals(lead: dict, prefs: dict | None) -> tuple[list[str], list[str]]:
    """Compute (hard_reject_reasons, soft_review_reasons) from preferences.

    Conservative and privacy-safe: returns only normalized reason CODES, never
    raw preference values. Empty prefs → no signals (cannot judge).
    """
    hard: list[str] = []
    soft: list[str] = []
    if not prefs:
        return hard, soft

    loc = (lead.get("location") or "").strip().lower()
    blocked = [b.lower() for b in (prefs.get("blocked_locations") or []) if isinstance(b, str)]
    preferred = [p.lower() for p in (prefs.get("preferred_locations") or []) if isinstance(p, str)]
    is_remote = "remote" in loc
    in_preferred = bool(loc) and any(p and (p in loc or loc in p) for p in preferred)

    # Blocked location is a hard conflict.
    if loc and any(b and b in loc for b in blocked):
        hard.append("blocked_location")

    # Remote / location-area gating.
    remote_only = bool(prefs.get("remote_only"))
    remote_matters = remote_only or bool(prefs.get("remote_preferred"))
    if loc:
        if not is_remote and not in_preferred:
            if remote_only:
                hard.append("remote_only_pref_conflict")
            elif remote_matters:
                soft.append("remote_pref_conflict")
    elif remote_matters:
        # No location metadata but remote preference matters → can't confirm.
        soft.append("location_ambiguous")

    # Compensation floor: reject only when the lead's stated upper bound is
    # clearly below the floor. Missing comp never rejects.
    floor = prefs.get("compensation_floor")
    if isinstance(floor, (int, float)) and not isinstance(floor, bool):
        lead_comp = parse_money(lead.get("compensation"))
        if lead_comp is not None and lead_comp < floor:
            hard.append("compensation_below_floor")

    # Work authorization: reject only on an explicit no-sponsorship statement
    # when the candidate requires sponsorship.
    if prefs.get("requires_sponsorship"):
        text = ((lead.get("raw_description") or "") + " " + loc).lower()
        if any(
            phrase in text
            for phrase in (
                "no sponsorship",
                "not sponsor",
                "without sponsorship",
                "not provide sponsorship",
                "unable to sponsor",
                "no visa sponsorship",
                "do not offer sponsorship",
            )
        ):
            hard.append("work_authorization_conflict")

    return hard, soft


# --------------------------------------------------------------------------- #
# Private preferences (markdown frontmatter) — never logged/committed
# --------------------------------------------------------------------------- #
# Source-file key -> normalized key. Normalized names are also accepted directly
# so sanitized fixtures can use either vocabulary.
_REMOTE_ONLY_VALUES = frozenset(
    {"remote_only", "remote-only", "remote only", "only remote", "fully remote", "strictly remote"}
)
# Safe keys allowed into the normalized prefs dict. Anything else is ignored.
_SAFE_NORMALIZED_KEYS = frozenset(
    {
        "remote_only",
        "remote_preferred",
        "blocked_locations",
        "preferred_locations",
        "current_location",
        "compensation_floor",
        "work_authorization",
        "requires_sponsorship",
        "relocation",
    }
)


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "required", "y", "1"):
            return True
        if v in ("false", "no", "none", "n", "0", "not required"):
            return False
    return None


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalize_preferences(raw: dict) -> dict:
    """Map a raw preferences mapping to the safe normalized prefs dict.

    Accepts both the private file's vocabulary (e.g. ``remote_preference``,
    ``minimum_compensation``, ``sponsorship_required``) and the normalized
    names directly. Unknown keys are ignored. Ambiguous values are left unset
    rather than guessed.
    """
    out: dict = {}

    # remote_preference / remote_only / remote_preferred
    remote_raw = raw.get("remote_preference")
    if "remote_only" in raw:
        b = _as_bool(raw.get("remote_only"))
        if b is not None:
            out["remote_only"] = b
            out["remote_preferred"] = b or bool(raw.get("remote_preferred"))
    if isinstance(remote_raw, str) and remote_raw.strip():
        rv = remote_raw.strip().lower()
        out.setdefault("remote_only", rv in _REMOTE_ONLY_VALUES)
        out.setdefault("remote_preferred", "remote" in rv)
    if "remote_preferred" in raw and "remote_preferred" not in out:
        b = _as_bool(raw.get("remote_preferred"))
        if b is not None:
            out["remote_preferred"] = b

    # locations
    blocked = _as_str_list(raw.get("blocked_locations"))
    if blocked:
        out["blocked_locations"] = blocked
    preferred = _as_str_list(raw.get("preferred_locations"))
    if preferred:
        out["preferred_locations"] = preferred
    current = raw.get("current_location")
    if isinstance(current, str) and current.strip():
        out["current_location"] = current.strip()

    # compensation floor (compensation_floor or minimum_compensation)
    comp_raw = raw.get("compensation_floor", raw.get("minimum_compensation"))
    comp = parse_money(comp_raw)
    if comp is not None:
        out["compensation_floor"] = comp

    # work authorization (kept for explicit-conflict detection; never printed)
    wa = raw.get("work_authorization")
    if isinstance(wa, str) and wa.strip():
        out["work_authorization"] = wa.strip()

    # sponsorship -> requires_sponsorship
    spon = _as_bool(raw.get("requires_sponsorship", raw.get("sponsorship_required")))
    if spon is not None:
        out["requires_sponsorship"] = spon

    # relocation (only if explicitly present; never invented)
    relo = raw.get("relocation")
    if isinstance(relo, str) and relo.strip():
        out["relocation"] = relo.strip()

    return {k: v for k, v in out.items() if k in _SAFE_NORMALIZED_KEYS}


def load_preferences_md(path: str | Path) -> dict:
    """Load private preferences from a markdown file's YAML frontmatter.

    Returns a normalized, privacy-safe prefs dict (see
    :func:`normalize_preferences`). Raises :class:`WatcherError` for a missing
    file or unparseable frontmatter so the caller can warn and continue.
    """
    p = Path(path)
    if not p.exists():
        raise WatcherError(f"preferences file not found: {p}")
    text = p.read_text(encoding="utf-8")
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    block = m.group(1) if m else text
    try:
        raw = _yaml_loads(block)
    except Exception as exc:  # malformed frontmatter
        raise WatcherError(f"could not parse preferences frontmatter: {exc}")
    if not isinstance(raw, dict):
        raise WatcherError("preferences frontmatter is not a mapping")
    return normalize_preferences(raw)


def preferences_summary(prefs: dict | None) -> dict:
    """A non-sensitive summary of which prefs are active (booleans/counts only).

    Safe to log or persist — carries no raw preference VALUES.
    """
    prefs = prefs or {}
    return {
        "remote_only": bool(prefs.get("remote_only")),
        "remote_preferred": bool(prefs.get("remote_preferred")),
        "blocked_locations_count": len(prefs.get("blocked_locations") or []),
        "preferred_locations_count": len(prefs.get("preferred_locations") or []),
        "compensation_floor_set": prefs.get("compensation_floor") is not None,
        "requires_sponsorship": bool(prefs.get("requires_sponsorship")),
        "current_location_set": bool(prefs.get("current_location")),
        "relocation_set": bool(prefs.get("relocation")),
    }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
_NEXT_ACTION = {
    "packet_ready": "generate_packet_then_human_submit",
    "needs_review": "human_review",
    "reject": "skip",
}


def _result(status: str, reasons: list[str]) -> dict:
    return {
        "status": status,
        "reasons": reasons,
        "recommend_packet": status == "packet_ready",
        "requires_human_review": status == "needs_review",
        "recommended_next_action": _NEXT_ACTION[status],
    }


def classify_readiness(
    *,
    lead: dict,
    route_decision: dict,
    freshness: dict,
    lane_ready: bool,
    already_packeted: bool,
    prefs: dict | None = None,
) -> dict:
    """Conservative first-pass readiness classification for one lead.

    Hard blockers (duplicate, stale, senior-only, location conflict, no ready
    lane, clear no-fit) reject outright. A lead reaches ``packet_ready`` only
    when the routed lane is ready_local, the resume exists, routing is
    high-confidence with no review flags, the fit is strong, freshness is a real
    posting timestamp inside the window, and metadata is sufficient. Anything
    plausible-but-uncertain lands in ``needs_review``.
    """
    fit = lead.get("fit_assessment") or {}
    recommendation = fit.get("fit_recommendation")
    missing = [m for m in (fit.get("missing_skills") or []) if m]
    title = lead.get("title") or ""
    basis = freshness.get("freshness_basis")
    within = freshness.get("within_window")

    resume_exists = bool(route_decision.get("selected_resume_exists"))
    route_review = bool(route_decision.get("needs_human_review"))
    confidence = route_decision.get("confidence")
    variant_id = route_decision.get("selected_variant_id")

    norm = lead.get("normalized_requirements") or {}
    metadata_ok = bool((lead.get("raw_description") or "").strip()) and bool(
        norm.get("keywords") or norm.get("required")
    )
    hard_prefs, soft_prefs = _preference_signals(lead, prefs)

    # ----- hard rejects (ordered: most decisive first) -----
    if already_packeted:
        return _result("reject", ["duplicate_existing_packet"])
    if basis != "unknown" and within is False:
        return _result("reject", [f"outside_lookback_window:{basis}"])
    if is_senior_only(title):
        return _result("reject", ["senior_staff_only"])
    if hard_prefs:
        return _result("reject", hard_prefs)
    if not lane_ready:
        return _result("reject", [f"no_ready_lane:{variant_id or 'none'}"])
    if recommendation == "no":
        return _result("reject", ["low_fit_recommendation"])

    # ----- needs_review accumulation (lane is ready; lead is plausible) -----
    reasons: list[str] = []
    if basis == "unknown":
        reasons.append("freshness_unknown")
    elif basis == "discovered_at":
        reasons.append("freshness_fallback_discovered_at")
    if not resume_exists:
        reasons.append("resume_source_missing")
    if route_review:
        for r in route_decision.get("review_reasons") or ["route_flagged"]:
            reasons.append(f"route:{r}")
    if confidence and confidence != "high":
        reasons.append(f"route_confidence_{confidence}")
    if recommendation == "maybe":
        reasons.append("fit_recommendation_maybe")
    elif recommendation != "strong_yes":
        reasons.append("lead_not_scored")
    if missing:
        reasons.append("skill_gaps:" + ",".join(missing[:6]))
    if not metadata_ok:
        reasons.append("sparse_metadata")
    if not (lead.get("location") or "").strip():
        reasons.append("location_ambiguous")
    reasons.extend(soft_prefs)

    if reasons:
        # Deduplicate while preserving order.
        seen: set[str] = set()
        ordered = [r for r in reasons if not (r in seen or seen.add(r))]
        return _result("needs_review", ordered)

    return _result("packet_ready", ["fit_strong_lane_ready_in_window"])


# --------------------------------------------------------------------------- #
# Queue assembly
# --------------------------------------------------------------------------- #
def build_queue_item(
    lead: dict,
    route_decision: dict,
    freshness: dict,
    classification: dict,
    *,
    lookback_hours: float,
) -> dict:
    """Assemble a single queue item from public lead metadata only.

    Carries no private profile content — just public posting metadata, lane
    IDs, scores, freshness, and reason strings.
    """
    fit = lead.get("fit_assessment") or {}
    return {
        "lead_id": lead.get("lead_id", ""),
        "source_id": lead.get("fingerprint", ""),
        "company": lead.get("company", ""),
        "title": (lead.get("title") or "").strip(),
        "source": lead.get("source", ""),
        "url": lead.get("posting_url") or lead.get("application_url") or "",
        "discovered_at": freshness.get("discovered_at"),
        "posted_at": freshness.get("posted_at"),
        "lookback_hours": lookback_hours,
        "freshness_basis": freshness.get("freshness_basis"),
        "timestamp_confidence": freshness.get("timestamp_confidence"),
        "age_hours": freshness.get("age_hours"),
        "route_variant_id": route_decision.get("selected_variant_id"),
        "route_confidence": route_decision.get("confidence"),
        "selected_lane": route_decision.get("selected_variant_id"),
        "selected_resume_exists": route_decision.get("selected_resume_exists"),
        "status": classification["status"],
        "score": fit.get("fit_score"),
        "fit_recommendation": fit.get("fit_recommendation"),
        "reasons": classification["reasons"],
        "recommended_next_action": classification["recommended_next_action"],
        "recommend_packet": classification["recommend_packet"],
        "requires_human_review": classification["requires_human_review"],
    }


def build_queue(
    leads: list[dict],
    *,
    registry: dict,
    now: datetime,
    since_hours: float,
    packeted_lead_ids: set[str] | None = None,
    max_candidates: int | None = None,
    prefs: dict | None = None,
    route_fn=route_lead,
    drop_stale: bool = True,
) -> dict:
    """Score-free queue builder over already-scored leads.

    Routes each lead, computes freshness, classifies readiness, and returns a
    ranked queue. ``drop_stale`` (default) removes leads that are clearly
    outside the window via a real timestamp so the artifact stays focused on
    genuinely new postings; their count is reported under ``dropped_stale``.
    ``max_candidates`` caps the number of emitted items after ranking.
    """
    packeted = packeted_lead_ids or set()
    items: list[dict] = []
    dropped_stale = 0

    for lead in leads:
        route_decision = route_fn(lead, registry)
        freshness = compute_freshness(lead, now=now, since_hours=since_hours)
        lead_id = lead.get("lead_id", "")
        classification = classify_readiness(
            lead=lead,
            route_decision=route_decision,
            freshness=freshness,
            lane_ready=lane_is_ready(registry, route_decision.get("selected_variant_id")),
            already_packeted=lead_id in packeted,
            prefs=prefs,
        )
        # Drop window-stale leads (known timestamp, outside window) from the
        # artifact rather than flooding it with rejects.
        stale = (
            classification["status"] == "reject"
            and classification["reasons"][:1] == [f"outside_lookback_window:{freshness.get('freshness_basis')}"]
        )
        if stale and drop_stale:
            dropped_stale += 1
            continue
        items.append(
            build_queue_item(
                lead, route_decision, freshness, classification, lookback_hours=since_hours
            )
        )

    items.sort(
        key=lambda it: (
            _STATUS_RANK.get(it["status"], 9),
            -(it["score"] if isinstance(it.get("score"), (int, float)) else -1),
        )
    )

    dropped_for_cap = 0
    if max_candidates is not None and len(items) > max_candidates:
        dropped_for_cap = len(items) - max_candidates
        items = items[:max_candidates]

    totals = {s: 0 for s in READINESS_STATUSES}
    for it in items:
        totals[it["status"]] = totals.get(it["status"], 0) + 1

    return {
        "schema_version": 1,
        "lookback_hours": since_hours,
        "totals": totals,
        "dropped_stale": dropped_stale,
        "dropped_for_cap": dropped_for_cap,
        "items": items,
    }
