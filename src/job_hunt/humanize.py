"""Humanize browser interactions for anti-bot resistance.

Pure, seeded samplers that produce a ``HumanizePlan`` the LLM agent executes
against ``mcp__Claude_in_Chrome__*`` tools during LinkedIn / Indeed Easy Apply
flows. Runtime humanization lives in the playbook markdown; this module owns
the randomness so behavior is deterministic and testable.

See ``docs/plans/2026-04-20-feat-humanize-browser-interactions-plan.md`` for
the full design. Key invariants:

- Humanization is detection-avoidance, not ToS compliance. The human still
  clicks the final Submit button on every application.
- All randomness is seeded via injected ``rng: random.Random``. No
  module-level RNG, no ``time.*`` / ``datetime.now`` / ``uuid`` / ``os.urandom``
  inside the samplers.
- Secret-adjacent arrays (``chunk_boundaries`` / ``chunk_delay_ms``) stay out
  of persisted attempt records. Callers must use
  :func:`redact_humanize_for_audit` before writing.
"""

from __future__ import annotations

import copy
import hashlib
import math
import random
import re
from typing import Any, Mapping, Sequence, TypedDict


# ---------------------------------------------------------------------------
# TypedDict contract — serialized to JSON in the handoff bundle.
# ---------------------------------------------------------------------------


class _HumanizePlanRequired(TypedDict):
    """Always-present base. ``enabled`` is the tolerant-consumer sentinel."""
    enabled: bool


class TypingSpec(TypedDict, total=False):
    mode: str  # "atomic" | "word_chunked" | "per_char_prefix"
    total_ms: int
    chunk_boundaries: list[int]
    chunk_delay_ms: list[int]


class PerFieldEntry(TypedDict):
    field_index: int
    pre_read_ms: int
    typing: TypingSpec
    post_fill_gap_ms: int


class PageAdvance(TypedDict):
    pre_click_ms: int
    post_fill_review_ms: int
    hover_dwell_ms: int


class ScrollPlan(TypedDict):
    passes: int  # 0 = no scroll
    per_pass_ms: list[int]


class MCPCallEstimate(TypedDict):
    per_field: list[int]
    total: int


class HumanizePlan(_HumanizePlanRequired, total=False):
    jd_read_ms: int
    scroll: ScrollPlan
    page_advance: PageAdvance
    per_field: list[PerFieldEntry]
    mcp_call_estimate: MCPCallEstimate


# ---------------------------------------------------------------------------
# Defaults — calibrated per Bours 2021 + Dhakal CHI'18 (136M keystrokes).
# ---------------------------------------------------------------------------


def _eligible_surfaces() -> list[str]:
    """Return the set of surfaces whose ``SurfaceSpec.humanize_eligible`` is True.

    Derived from the registry at import time so the allowlist has a single
    source of truth.
    """
    from .surfaces.registry import _SURFACE_SPECS

    return sorted(
        surface
        for surface, spec in _SURFACE_SPECS.items()
        if getattr(spec, "humanize_eligible", False)
    )


HUMANIZE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "surfaces": _eligible_surfaces(),
    "read": {
        "wpm": 220,
        "min_ms": 600,
        "max_ms": 25_000,
        "variance": 0.45,
        "skim_probability": 0.20,
    },
    "typing": {
        "mode": "word_chunked",  # {"atomic", "word_chunked", "per_char_prefix"}
        "mean_ms_per_char": 170,  # ≈40 WPM, population median
        "stddev_ms_per_char": 65,
        "min_ms": 45,
        "max_ms": 900,
        "word_boundary_pause_ms": [120, 380],
        "punctuation_pause_ms": [250, 700],
        "sentence_boundary_pause_ms": [500, 1500],
        "correction_rate_per_100_chars": 2.5,
    },
    "field_gap_ms": [500, 3500],
    "page_advance": {
        "pre_click_ms": [1200, 3600],
        "post_fill_review_ms": [2000, 6000],
        "hover_dwell_ms": [220, 780],
    },
    "scroll": {
        "probability": 0.65,
        "passes": [1, 3],
        "per_pass_ms": [400, 1400],
    },
}


# Defense-in-depth absolute ceilings (regardless of policy overrides). The
# playbook also caps per-sleep at 60 000 ms; these are the plan-level final
# clamps applied by :func:`validate_humanize_plan`.
_ABSOLUTE_MAX_SLEEP_MS = 60_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def seed_from_draft_id(draft_id: str) -> int:
    """Derive a deterministic 64-bit seed from a draft id."""
    digest = hashlib.sha256(draft_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _lognormal_params_from_moments(
    mean: float, stddev: float
) -> tuple[float, float]:
    """Convert output-space (mean, stddev) to log-space (mu, sigma).

    ``random.lognormvariate(mu, sigma)`` takes log-space parameters; the
    typical bug is passing the target mean as ``mu``. This helper applies
    the closed-form conversion:

        sigma^2 = ln(1 + (s/m)^2)
        mu      = ln(m) - sigma^2 / 2
    """
    if mean <= 0:
        raise ValueError(f"mean must be positive, got {mean}")
    if stddev < 0:
        raise ValueError(f"stddev must be non-negative, got {stddev}")
    variance = stddev * stddev
    sigma_sq = math.log(1.0 + variance / (mean * mean))
    mu = math.log(mean) - sigma_sq / 2.0
    return mu, math.sqrt(sigma_sq)


def _coerce_word_count(value: Any) -> int:
    """Clamp an untrusted ``visible_text_word_count`` to a sane range."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    if n < 0:
        return 0
    if n > 100_000:
        return 100_000
    return n


def _clamp(value: int, lower: int, upper: int) -> int:
    if lower > upper:
        lower, upper = upper, lower
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _uniform_int(rng: random.Random, lo: int, hi: int) -> int:
    if lo > hi:
        lo, hi = hi, lo
    return int(rng.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Samplers
# ---------------------------------------------------------------------------


def _sample_read_time_ms(
    word_count: int,
    policy: Mapping[str, Any],
    *,
    rng: random.Random,
    min_ms_override: int | None = None,
) -> int:
    """Sample a read-time delay in ms for text of ``word_count`` words.

    ``min_ms_override`` lets callers lower the floor for content the user has
    already reviewed (e.g. a curated yes/no answer — no reading time needed).
    """
    read = policy["read"]
    wpm = float(read["wpm"])
    min_ms = int(read["min_ms"]) if min_ms_override is None else int(min_ms_override)
    max_ms = int(read["max_ms"])
    variance = float(read.get("variance", 0.35))
    skim_prob = float(read.get("skim_probability", 0.0))

    words = max(0, int(word_count))
    baseline_ms = int((words / wpm) * 60_000) if wpm > 0 else 0

    jitter = 1.0 + rng.uniform(-variance, variance)
    sampled = int(baseline_ms * jitter)
    if skim_prob > 0 and rng.random() < skim_prob:
        sampled = int(sampled * 0.3)
    return _clamp(sampled, min_ms, max_ms)


# Provenance values for which the user has pre-reviewed the exact answer.
# Read-time floor drops for these — no in-the-moment reading needed.
_PREREVIEWED_PROVENANCES = frozenset({"curated"})
_PREREVIEWED_READ_FLOOR_MS = 300


def _split_chunk_boundaries(answer: str) -> list[int]:
    """Return char indices where ``word_chunked`` mode should commit a prefix."""
    boundaries: list[int] = []
    for i, ch in enumerate(answer):
        if ch in (" ", "\t", "\n", ".", ",", ";", ":", "!", "?"):
            # commit prefix up through this delimiter
            if i + 1 <= len(answer) and (not boundaries or boundaries[-1] != i + 1):
                boundaries.append(i + 1)
    # Always include the full length as the final boundary.
    if not boundaries or boundaries[-1] != len(answer):
        boundaries.append(len(answer))
    return boundaries


# Answers that look like URLs or email addresses should be typed atomically —
# humans paste them as a unit, not word-by-word. Chunking a URL splits on
# slashes/colons/dots which produces an obviously-bot cadence.
_URL_LIKE_RE = re.compile(
    r"^\s*(?:https?://|www\.|mailto:|[\w.+-]+@)", re.IGNORECASE
)


def _is_atomic_paste_answer(answer: str) -> bool:
    """True if the answer should be committed in one shot regardless of mode."""
    if not answer:
        return False
    return bool(_URL_LIKE_RE.match(answer))


def _sample_typing_spec(
    answer: str, policy: Mapping[str, Any], *, rng: random.Random
) -> TypingSpec:
    """Sample a typing plan for a given answer string."""
    typing_policy = policy["typing"]
    mode = str(typing_policy.get("mode", "word_chunked"))
    if mode not in {"atomic", "word_chunked", "per_char_prefix"}:
        raise ValueError(f"unknown typing.mode: {mode!r}")

    # Override: URL- and email-shaped answers always commit atomically.
    # Humans paste these; chunking them is a bot tell.
    if _is_atomic_paste_answer(answer):
        mode = "atomic"

    mean_ms = float(typing_policy["mean_ms_per_char"])
    stddev_ms = float(typing_policy["stddev_ms_per_char"])
    min_ms = int(typing_policy["min_ms"])
    max_ms = int(typing_policy["max_ms"])

    answer = answer or ""
    n = len(answer)
    if n == 0:
        spec: TypingSpec = {"mode": mode, "total_ms": 0,
                            "chunk_boundaries": [], "chunk_delay_ms": []}
        return spec

    if mean_ms <= 0:
        total_ms = min_ms
    else:
        mu, sigma = _lognormal_params_from_moments(mean_ms, stddev_ms)
        # Sample per-char then sum; clamp each draw to [min_ms, max_ms].
        per_char_samples = [
            _clamp(int(rng.lognormvariate(mu, sigma)), min_ms, max_ms)
            for _ in range(n)
        ]
        total_ms = sum(per_char_samples)

    if mode == "atomic":
        # Atomic mode pastes the answer in one shot — nothing sleeps for the
        # sampled per-char budget, so reporting it as total_ms would mislead
        # the playbook / audit. Report 0 to reflect actual wall-clock cost.
        return {"mode": mode, "total_ms": 0,
                "chunk_boundaries": [], "chunk_delay_ms": []}

    # word_chunked / per_char_prefix both need boundary/delay arrays. For
    # per_char_prefix, every char is its own chunk.
    if mode == "per_char_prefix":
        boundaries = list(range(1, n + 1))
    else:
        boundaries = _split_chunk_boundaries(answer)

    # Delay after each chunk (including last, which is the post-commit pause).
    word_pause = typing_policy["word_boundary_pause_ms"]
    punct_pause = typing_policy["punctuation_pause_ms"]
    sentence_pause = typing_policy["sentence_boundary_pause_ms"]

    delays: list[int] = []
    for idx, boundary in enumerate(boundaries):
        if boundary <= 0 or boundary > n:
            delay = _uniform_int(rng, word_pause[0], word_pause[1])
        else:
            # Classify the last committed character.
            ch = answer[boundary - 1]
            if ch in (".", "!", "?"):
                delay = _uniform_int(rng, sentence_pause[0], sentence_pause[1])
            elif ch in (",", ";", ":"):
                delay = _uniform_int(rng, punct_pause[0], punct_pause[1])
            else:
                delay = _uniform_int(rng, word_pause[0], word_pause[1])
        delays.append(delay)

    return {
        "mode": mode,
        "total_ms": total_ms,
        "chunk_boundaries": boundaries,
        "chunk_delay_ms": delays,
    }


def _sample_page_advance(
    policy: Mapping[str, Any], *, rng: random.Random
) -> PageAdvance:
    page = policy["page_advance"]
    return {
        "pre_click_ms": _uniform_int(rng, *page["pre_click_ms"]),
        "post_fill_review_ms": _uniform_int(rng, *page["post_fill_review_ms"]),
        "hover_dwell_ms": _uniform_int(rng, *page["hover_dwell_ms"]),
    }


def _sample_scroll_plan(
    policy: Mapping[str, Any], *, rng: random.Random
) -> ScrollPlan:
    """Always returns a ScrollPlan; ``passes: 0`` when scroll is skipped."""
    scroll = policy["scroll"]
    probability = float(scroll.get("probability", 0.0))
    if probability <= 0 or rng.random() >= probability:
        return {"passes": 0, "per_pass_ms": []}
    passes = _uniform_int(rng, *scroll["passes"])
    per_pass = [_uniform_int(rng, *scroll["per_pass_ms"]) for _ in range(passes)]
    return {"passes": passes, "per_pass_ms": per_pass}


def _estimate_mcp_calls(
    per_field: Sequence[PerFieldEntry], scroll: ScrollPlan
) -> MCPCallEstimate:
    """Rough estimate of extra MCP calls introduced by humanization."""
    per_field_counts: list[int] = []
    for entry in per_field:
        typing = entry.get("typing") or {}
        boundaries = typing.get("chunk_boundaries") or []
        # Each chunk = one form_input call. Atomic mode has empty boundaries
        # but still costs 1 form_input.
        per_field_counts.append(max(1, len(boundaries)))
    total = sum(per_field_counts) + scroll.get("passes", 0)
    return {"per_field": per_field_counts, "total": total}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_humanize_plan(
    fields: Sequence[Mapping[str, Any]],
    page_info: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    rng: random.Random,
) -> HumanizePlan:
    """Build a ``HumanizePlan`` from the given fields, page info, and policy.

    When ``policy['enabled']`` is falsy, returns a minimal plan with only
    ``{"enabled": False}`` — callers (and the playbook) treat this as a
    no-op.
    """
    if not policy.get("enabled", False):
        return {"enabled": False}

    word_count = _coerce_word_count(page_info.get("visible_text_word_count", 0))
    jd_read_ms = _sample_read_time_ms(word_count, policy, rng=rng)
    scroll = _sample_scroll_plan(policy, rng=rng)
    page_advance = _sample_page_advance(policy, rng=rng)

    gap_lo, gap_hi = policy["field_gap_ms"]

    per_field_entries: list[PerFieldEntry] = []
    for idx, field in enumerate(fields):
        question = str(
            field.get("question")
            or field.get("question_text")
            or field.get("label")
            or ""
        )
        answer = str(field.get("answer") or "")
        provenance = str(field.get("provenance") or "")
        question_words = max(1, len(question.split()))
        read_floor: int | None = None
        if provenance in _PREREVIEWED_PROVENANCES:
            read_floor = _PREREVIEWED_READ_FLOOR_MS
        pre_read_ms = _sample_read_time_ms(
            question_words, policy, rng=rng, min_ms_override=read_floor,
        )
        typing = _sample_typing_spec(answer, policy, rng=rng)
        post_fill_gap_ms = _uniform_int(rng, int(gap_lo), int(gap_hi))
        per_field_entries.append({
            "field_index": idx,
            "pre_read_ms": pre_read_ms,
            "typing": typing,
            "post_fill_gap_ms": post_fill_gap_ms,
        })

    plan: HumanizePlan = {
        "enabled": True,
        "jd_read_ms": jd_read_ms,
        "page_advance": page_advance,
        "per_field": per_field_entries,
    }
    if scroll["passes"] > 0:
        plan["scroll"] = scroll
    plan["mcp_call_estimate"] = _estimate_mcp_calls(per_field_entries, scroll)
    return plan


def validate_humanize_plan(
    plan: HumanizePlan, policy: Mapping[str, Any]
) -> HumanizePlan:
    """Re-clamp every numeric sleep in the plan to ``_ABSOLUTE_MAX_SLEEP_MS``.

    Idempotent. Defensive layer against bundle tampering between
    ``prepare_application`` and ``apply_posting``. Does not mutate the input.
    """
    if not plan.get("enabled", False):
        return {"enabled": False}

    clamped: HumanizePlan = {"enabled": True}

    if "jd_read_ms" in plan:
        clamped["jd_read_ms"] = _clamp(int(plan["jd_read_ms"]), 0, _ABSOLUTE_MAX_SLEEP_MS)

    if "scroll" in plan:
        scroll = plan["scroll"]
        clamped["scroll"] = {
            "passes": max(0, int(scroll.get("passes", 0))),
            "per_pass_ms": [
                _clamp(int(ms), 0, _ABSOLUTE_MAX_SLEEP_MS)
                for ms in scroll.get("per_pass_ms", [])
            ],
        }

    if "page_advance" in plan:
        page = plan["page_advance"]
        clamped["page_advance"] = {
            "pre_click_ms": _clamp(int(page["pre_click_ms"]), 0, _ABSOLUTE_MAX_SLEEP_MS),
            "post_fill_review_ms": _clamp(int(page["post_fill_review_ms"]), 0, _ABSOLUTE_MAX_SLEEP_MS),
            "hover_dwell_ms": _clamp(int(page["hover_dwell_ms"]), 0, _ABSOLUTE_MAX_SLEEP_MS),
        }

    if "per_field" in plan:
        clamped_fields: list[PerFieldEntry] = []
        for entry in plan["per_field"]:
            typing = entry.get("typing") or {}
            clamped_typing: TypingSpec = {
                "mode": str(typing.get("mode", "atomic")),
                "total_ms": _clamp(int(typing.get("total_ms", 0)), 0, _ABSOLUTE_MAX_SLEEP_MS * 10),
                "chunk_boundaries": [int(b) for b in typing.get("chunk_boundaries", [])],
                "chunk_delay_ms": [
                    _clamp(int(ms), 0, _ABSOLUTE_MAX_SLEEP_MS)
                    for ms in typing.get("chunk_delay_ms", [])
                ],
            }
            clamped_fields.append({
                "field_index": int(entry["field_index"]),
                "pre_read_ms": _clamp(int(entry["pre_read_ms"]), 0, _ABSOLUTE_MAX_SLEEP_MS),
                "typing": clamped_typing,
                "post_fill_gap_ms": _clamp(int(entry["post_fill_gap_ms"]), 0, _ABSOLUTE_MAX_SLEEP_MS),
            })
        clamped["per_field"] = clamped_fields

    if "mcp_call_estimate" in plan:
        est = plan["mcp_call_estimate"]
        clamped["mcp_call_estimate"] = {
            "per_field": [max(0, int(n)) for n in est.get("per_field", [])],
            "total": max(0, int(est.get("total", 0))),
        }

    return clamped


def redact_humanize_for_audit(plan: HumanizePlan) -> dict:
    """Strip secret-adjacent arrays before persisting to an attempt record.

    ``chunk_boundaries`` and ``chunk_delay_ms`` are derived from typed answer
    content — their length and positions leak the word-structure of fields
    that may contain secrets (passwords, security-question answers). Strip
    them and keep only counts + modes + top-level plan shape.
    """
    if not plan.get("enabled", False):
        return {"enabled": False}
    redacted: dict[str, Any] = {"enabled": True}
    for key in ("jd_read_ms",):
        if key in plan:
            redacted[key] = plan[key]
    if "scroll" in plan:
        redacted["scroll"] = dict(plan["scroll"])
    if "page_advance" in plan:
        redacted["page_advance"] = dict(plan["page_advance"])
    if "mcp_call_estimate" in plan:
        redacted["mcp_call_estimate"] = copy.deepcopy(plan["mcp_call_estimate"])
    if "per_field" in plan:
        safe_fields: list[dict[str, Any]] = []
        for entry in plan["per_field"]:
            typing = entry.get("typing") or {}
            chunk_count = len(typing.get("chunk_boundaries") or [])
            safe_fields.append({
                "field_index": entry["field_index"],
                "pre_read_ms": entry["pre_read_ms"],
                "post_fill_gap_ms": entry["post_fill_gap_ms"],
                "typing": {
                    "mode": typing.get("mode", "atomic"),
                    "total_ms": typing.get("total_ms", 0),
                    "chunk_count": chunk_count,
                },
            })
        redacted["per_field"] = safe_fields
    return redacted
