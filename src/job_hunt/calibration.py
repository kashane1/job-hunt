"""Scoring calibration — close the learning loop without auto-mutation.

This is the Phase-4 learning loop the brainstorm called for. The three
analytics reports (dashboard, skills-gap, rejection-patterns) describe what
the world did with the applications; this module turns those observations
into **proposed** edits to ``config/scoring.yaml`` plus profile/answer-bank
evidence suggestions.

Hard invariant — propose only, never apply (mirrors the human-submit
invariant): :func:`propose_calibration` is read-only with respect to
``config/scoring.yaml``, the candidate profile, and the answer bank. Its
only side effect is writing a proposal artifact under
``data/calibration/``. A human reads the proposal and edits scoring.yaml by
hand. There is deliberately no ``--apply`` path: an automated scoring loop
that mutates its own selection criteria from a small, biased sample is
exactly the failure mode the trust-first posture exists to prevent.

Every proposal carries the evidence and the confidence of the signal it
came from, using the same sample-size discipline as :mod:`analytics`
(``insufficient_data`` < ``low`` < ``ok``). Insufficient signals produce no
proposals — silence beats a confident wrong nudge.

Internal module — raises ValueError directly per the batch-1 convention
(same as :mod:`analytics`, which this consumes).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .analytics import (
    report_dashboard,
    report_rejection_patterns,
    report_skills_gap,
)
from .utils import ensure_dir, now_iso, write_json

# Confidence ordering shared with analytics' sample-size gates.
_CONFIDENCE_RANK: Final = {"insufficient_data": 0, "low": 1, "ok": 2}

# Proposal thresholds — deliberately conservative. Calibration suggests a
# direction backed by evidence; it does not compute an "optimum" from a
# sample too small to support one.
_MIN_SKILL_PRIORITY: Final = 0.5      # skills_gap priority_score floor
_MAX_SKILL_PROPOSALS: Final = 10
_LOW_CALLBACK_RATE: Final = 0.15      # below this, the funnel is too leaky
_HIGH_APPLIED_DROPOFF: Final = 0.70   # frac of rejections stuck at "applied"
_DOMINANT_REJECTION_FRACTION: Final = 0.50
_MIN_DOMINANT_REJECTION_ABS: Final = 5
_THRESHOLD_STEP: Final = 5
_STRONG_YES_CEILING: Final = 90
_MAYBE_CEILING: Final = 80

CALIBRATION_PROPOSAL_SCHEMA_VERSION: Final = 1


def _weakest(*confidences: str) -> str:
    """Return the weakest (most cautious) confidence label among inputs."""
    present = [c for c in confidences if c in _CONFIDENCE_RANK]
    if not present:
        return "insufficient_data"
    return min(present, key=lambda c: _CONFIDENCE_RANK[c])


def _has_rates(report: dict) -> bool:
    """A report is rate-bearing when it cleared its own sample-size gate."""
    return report.get("confidence", "insufficient_data") != "insufficient_data"


def _skill_keyword_proposals(
    gap: dict,
    rej: dict,
    scoring_config: dict,
) -> list[dict]:
    """Skills that recur as gaps in scored leads AND show up in rejected
    applications, but are not yet scoring keywords. Cross-confirming the
    skills-gap signal against actual rejections avoids chasing a skill the
    profile simply chooses not to pursue."""
    if not _has_rates(gap):
        return []
    existing = {str(k).lower() for k in scoring_config.get("skill_keywords", [])}
    rejected_missing = {
        str(skill).lower(): count
        for skill, count in rej.get("top_missing_skills_in_rejected", [])
    } if _has_rates(rej) else {}

    proposals: list[dict] = []
    for entry in gap.get("gaps", []):
        skill = str(entry.get("skill", "")).lower()
        if not skill or skill in existing:
            continue
        if entry.get("priority_score", 0) < _MIN_SKILL_PRIORITY:
            continue
        if skill not in rejected_missing:
            continue
        proposals.append({
            "target": "config/scoring.yaml",
            "key": "skill_keywords",
            "change": "add",
            "value": skill,
            "rationale": (
                f"'{skill}' is missing in {entry['frequency']} scored leads "
                f"(avg fit {entry['avg_fit_score']}) and appears in "
                f"{rejected_missing[skill]} rejected applications, but is not a "
                f"scoring keyword. Adding it makes fit scores reflect this "
                f"recurring requirement."
            ),
            "evidence": {
                "gap_frequency": entry["frequency"],
                "gap_avg_fit_score": entry["avg_fit_score"],
                "gap_priority_score": entry["priority_score"],
                "rejected_occurrences": rejected_missing[skill],
            },
            "source_signal": "skills_gap+rejections",
            "confidence": _weakest(gap["confidence"], rej.get("confidence", "insufficient_data")),
        })
        if len(proposals) >= _MAX_SKILL_PROPOSALS:
            break
    return proposals


def _negative_keyword_proposals(rej: dict, scoring_config: dict) -> list[dict]:
    """If one remote-policy class dominates rejections, surface it as a
    candidate negative keyword (so the funnel stops spending applications on
    a class that consistently fails)."""
    if not _has_rates(rej):
        return []
    by_remote = rej.get("rejected_by_remote_policy", {})
    rejected_total = rej.get("breakdown", {}).get("rejected", 0)
    if not by_remote or not rejected_total:
        return []
    existing = {str(k).lower() for k in scoring_config.get("negative_keywords", [])}
    proposals: list[dict] = []
    for policy, count in by_remote.items():
        token = str(policy).lower().strip()
        if token in ("", "unknown") or token in existing:
            continue
        frac = count / rejected_total
        if frac < _DOMINANT_REJECTION_FRACTION or count < _MIN_DOMINANT_REJECTION_ABS:
            continue
        proposals.append({
            "target": "config/scoring.yaml",
            "key": "negative_keywords",
            "change": "add",
            "value": token,
            "rationale": (
                f"{round(frac * 100)}% of rejections ({count}/{rejected_total}) "
                f"came from companies with remote_policy '{policy}'. Treating "
                f"'{token}' as a negative keyword down-ranks that class."
            ),
            "evidence": {"rejected_with_policy": count, "rejected_total": rejected_total},
            "source_signal": "rejections",
            "confidence": rej["confidence"],
        })
    return proposals


def _threshold_proposals(dash: dict, rej: dict, scoring_config: dict) -> list[dict]:
    """If callbacks are rare and most rejections die at 'applied', the funnel
    is admitting too many weak-fit leads — propose a one-step tightening of
    the fit thresholds (bounded; a direction, not an optimum)."""
    if not (_has_rates(dash) and _has_rates(rej)):
        return []
    callback_rate = dash.get("callback_rate")
    if callback_rate is None or callback_rate >= _LOW_CALLBACK_RATE:
        return []
    drop_off = rej.get("drop_off_by_stage", {})
    rejected_total = rej.get("breakdown", {}).get("rejected", 0)
    if not rejected_total:
        return []
    applied_dropoff_frac = drop_off.get("applied", 0) / rejected_total
    if applied_dropoff_frac <= _HIGH_APPLIED_DROPOFF:
        return []

    conf = _weakest(dash["confidence"], rej["confidence"])
    proposals: list[dict] = []
    for key, ceiling, default in (
        ("strong_yes_threshold", _STRONG_YES_CEILING, 75),
        ("maybe_threshold", _MAYBE_CEILING, 55),
    ):
        current = int(scoring_config.get(key, default))
        proposed = min(current + _THRESHOLD_STEP, ceiling)
        if proposed == current:
            continue
        proposals.append({
            "target": "config/scoring.yaml",
            "key": key,
            "change": "raise",
            "current": current,
            "value": proposed,
            "rationale": (
                f"callback_rate is {callback_rate} (< {_LOW_CALLBACK_RATE}) and "
                f"{round(applied_dropoff_frac * 100)}% of rejections never get "
                f"past 'applied'. Raising {key} {current}→{proposed} makes the "
                f"pipeline more selective. One bounded step — re-run "
                f"calibrate-scoring after more outcomes to decide further."
            ),
            "evidence": {
                "callback_rate": callback_rate,
                "applied_dropoff_fraction": round(applied_dropoff_frac, 3),
                "rejected_total": rejected_total,
            },
            "source_signal": "dashboard+rejections",
            "confidence": conf,
        })
    return proposals


def _profile_evidence_suggestions(gap: dict) -> list[dict]:
    """High-priority skill gaps become 'add evidence' work for the human —
    NOT auto-written answer-bank entries (that corpus is human-reviewed)."""
    if not _has_rates(gap):
        return []
    out: list[dict] = []
    for entry in gap.get("gaps", []):
        if entry.get("priority_score", 0) < _MIN_SKILL_PRIORITY:
            continue
        out.append({
            "skill": entry["skill"],
            "frequency": entry["frequency"],
            "avg_fit_score": entry["avg_fit_score"],
            "suggestion": (
                f"Roles needing '{entry['skill']}' recur in high-fit leads. If "
                f"you have real experience, add a profile highlight / answer-bank "
                f"entry citing it; if not, this is a genuine skill gap."
            ),
            "evidence_lead_ids": entry.get("evidence_lead_ids", []),
        })
    return out


def propose_calibration(
    data_root: Path,
    profile: dict,
    scoring_config: dict,
    *,
    taxonomy_path: Path | None = None,
    excluded_path: Path | None = None,
    out_dir: Path | None = None,
) -> dict:
    """Turn the three analytics signals into a reviewable calibration
    proposal. Read-only w.r.t. config/profile/answer-bank; the only write is
    the proposal artifact under ``out_dir`` (default ``data_root/calibration``).
    """
    dash = report_dashboard(data_root)
    gap = report_skills_gap(
        data_root, profile,
        taxonomy_path=taxonomy_path, excluded_path=excluded_path,
    )
    rej = report_rejection_patterns(data_root)

    overall_confidence = _weakest(
        dash.get("confidence", "insufficient_data"),
        gap.get("confidence", "insufficient_data"),
        rej.get("confidence", "insufficient_data"),
    )

    proposals: list[dict] = []
    proposals += _skill_keyword_proposals(gap, rej, scoring_config)
    proposals += _negative_keyword_proposals(rej, scoring_config)
    proposals += _threshold_proposals(dash, rej, scoring_config)

    result = {
        "schema_version": CALIBRATION_PROPOSAL_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "overall_confidence": overall_confidence,
        "apply_policy": "manual_human_edit_only",
        "signal_confidence": {
            "dashboard": dash.get("confidence", "insufficient_data"),
            "skills_gap": gap.get("confidence", "insufficient_data"),
            "rejection_patterns": rej.get("confidence", "insufficient_data"),
        },
        "scoring_proposals": proposals,
        "profile_evidence_suggestions": _profile_evidence_suggestions(gap),
        "recommendations": list(rej.get("observations", [])),
        "variant_rates": dash.get("variant_rates", {}),
    }
    if not proposals and overall_confidence == "insufficient_data":
        result["guidance"] = (
            "Not enough outcome data for any proposal. Keep applying and "
            "recording status transitions; re-run when analytics clears the "
            "sample-size gates."
        )

    target_dir = out_dir if out_dir is not None else (data_root / "calibration")
    ensure_dir(target_dir)
    stamp = result["generated_at"].replace(":", "").replace("-", "")
    json_path = target_dir / f"{stamp}-scoring-proposal.json"
    write_json(json_path, result)
    (target_dir / f"{stamp}-scoring-proposal.md").write_text(
        _render_markdown(result), encoding="utf-8",
    )
    result["proposal_path"] = str(json_path)
    return result


def _render_markdown(result: dict) -> str:
    lines = [
        "# Scoring calibration proposal",
        "",
        f"- generated_at: {result['generated_at']}",
        f"- overall_confidence: **{result['overall_confidence']}**",
        f"- apply_policy: **{result['apply_policy']}** "
        "(edit config/scoring.yaml by hand; this tool never writes it)",
        "",
        "## Signal confidence",
        "",
    ]
    for name, conf in result["signal_confidence"].items():
        lines.append(f"- {name}: {conf}")
    lines.append("")
    lines.append("## Proposed scoring.yaml changes")
    lines.append("")
    if not result["scoring_proposals"]:
        lines.append("_None — signals insufficient or no change warranted._")
    for p in result["scoring_proposals"]:
        head = f"- **{p['key']}** {p['change']} `{p['value']}`"
        if "current" in p:
            head += f" (current: `{p['current']}`)"
        lines.append(head)
        lines.append(f"  - confidence: {p['confidence']} | source: {p['source_signal']}")
        lines.append(f"  - {p['rationale']}")
    lines.append("")
    lines.append("## Profile / answer-bank evidence suggestions")
    lines.append("")
    if not result["profile_evidence_suggestions"]:
        lines.append("_None._")
    for s in result["profile_evidence_suggestions"]:
        lines.append(f"- **{s['skill']}** (×{s['frequency']}, avg fit {s['avg_fit_score']}): {s['suggestion']}")
    if result.get("recommendations"):
        lines.append("")
        lines.append("## Observations")
        lines.append("")
        for obs in result["recommendations"]:
            lines.append(f"- {obs}")
    if result.get("guidance"):
        lines.append("")
        lines.append(f"> {result['guidance']}")
    lines.append("")
    return "\n".join(lines)
