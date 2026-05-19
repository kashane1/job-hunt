"""Inbound email → lead-status triage: the world→ledger→calibrate bridge.

`calibrate-scoring` only reads Model B (`{lead_id}-status.json`, written by
`tracking`). `confirmation.py` verifies inbound email but writes Model A
(`{draft_id}/status.json`). This module is the **bridge**: it reuses
`confirmation`'s verification/parsing, classifies recruiter/ATS outcome
email, and advances Model B idempotently, under a single lock, with no
backward motion — so the learning loop is finally fed.

Trust posture (compile-time, not config — see AGENTS.md):
- verification-gated (allowlist OR DKIM ``d=`` registrable-domain equal to
  the *stored* lead company domain; display name / body never trusted);
- ambiguous / unverified → ``_suspicious/`` quarantine, zero ledger writes;
- idempotent across both models keyed on the same ``event_id``;
- no backward stage motion; outcomes from non-allowlisted senders are
  quarantine-then-human-promote, never silent (anti-spoof);
- propose-only downstream: triage feeds Model B; it never tunes scoring.

This is a CLI-wrapped verification boundary, so it raises
``TriageError(StructuredError)`` (the CLI emits ``exc.to_dict()``), NOT the
bare ``ValueError`` used by purely-internal modules.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .analytics import STAGE_SEQUENCE
from .confirmation import ParsedEmail
from .tracking import (
    SEMI_TERMINAL_STAGES,
    TERMINAL_STAGES,
    VALID_STAGES,
    _apply_transition_locked,
)
from .utils import (
    FileLockContentionError,
    StructuredError,
    file_lock,
    now_iso,
    read_json,
    repo_root,
)

TRIAGE_ERROR_CODES: Final = frozenset({
    "triage_low_confidence",
    "triage_ambiguous_correlation",
    "triage_no_correlation",
    "triage_status_locked",
    "triage_unbridged",
    "triage_sender_unverified",
    "triage_invalid_input",
})


class TriageError(StructuredError):
    """Structured error for triage failures (CLI emits to_dict())."""

    ALLOWED_ERROR_CODES = TRIAGE_ERROR_CODES


# =============================================================================
# Stage ladder — single source of truth is analytics.STAGE_SEQUENCE
# =============================================================================

# Forward progression ladder. `not_applied` sits below `applied`; the rest
# mirrors analytics.STAGE_SEQUENCE EXACTLY (asserted by a consistency test)
# so triage, analytics, and confirmation never disagree on ordering.
STAGE_LADDER: Final[dict[str, int]] = {
    "not_applied": 0,
    **{stage: i + 1 for i, stage in enumerate(STAGE_SEQUENCE)},
}

# Outcomes are not ladder-ranked; they may be reached from any non-terminal
# stage (a real rejection/ghost can arrive at any point).
OUTCOME_STAGES: Final = frozenset({"rejected", "withdrawn", "ghosted"})

# confirmation.EventType → Model-B stage. interview→onsite is a deliberate
# constant (Indeed/ATS "interview" emails rarely distinguish phone vs onsite;
# the recruiter classifier in Phase 2 refines phone_screen separately).
_EVENT_TO_STAGE: Final[dict[str, str]] = {
    "submitted": "applied",
    "confirmed": "applied",
    "interview": "onsite",
    "offer": "offer",
    "rejected": "rejected",
    "ghosted": "ghosted",
}


def event_id_for(parsed: ParsedEmail) -> str:
    """Idempotency key — IDENTICAL formula to confirmation.update_status so
    Model A and Model B dedup on the same identity."""
    source_id = f"gmail:{parsed.message_id or '<no-id>'}"
    return hashlib.sha256(
        f"{source_id}:{parsed.event_type}".encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class BridgeResult:
    outcome: Literal[
        "advanced",
        "noop_backward",
        "noop_duplicate",
        "noop_terminal",
        "skipped_contention",
        "quarantined",
    ]
    lead_id: str | None
    from_stage: str | None
    to_stage: str | None
    event_id: str
    inferred_skip: bool = False

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _status_path(data_root: Path, lead_id: str) -> Path:
    return data_root / "applications" / f"{lead_id}-status.json"


def _baseline_status(lead_id: str) -> dict:
    """In-memory not_applied record (written once, under the lock, if the
    status file does not exist yet — create+apply must not be two writes)."""
    ts = now_iso()
    return {
        "lead_id": lead_id,
        "current_stage": "not_applied",
        "transitions": [],
        "generated_content_ids": [],
        "follow_up": {
            "next_follow_up_date": "",
            "follow_up_count": 0,
            "suppress_follow_up": False,
        },
        "outcome_notes": "",
        "created_at": ts,
        "updated_at": ts,
    }


def bridge_event(
    parsed: ParsedEmail,
    *,
    lead_id: str,
    data_root: Path | None = None,
) -> BridgeResult:
    """Apply a verified, correlated email to Model B (`{lead_id}-status.json`).

    Pure A→B function: the caller resolves identity and has already verified
    + correlated the email. The whole pre-validate-and-write happens under a
    SINGLE ``file_lock`` acquisition (no second lock anywhere) so a
    concurrent manual ``update-status`` cannot race the decision.
    """
    data_root = data_root or (repo_root() / "data")
    status_path = _status_path(data_root, lead_id)
    eid = event_id_for(parsed)
    target = _EVENT_TO_STAGE.get(parsed.event_type)
    if target is None or target not in VALID_STAGES:
        raise TriageError(
            f"Unmapped event_type {parsed.event_type!r}",
            error_code="triage_invalid_input",
            remediation="Extend _EVENT_TO_STAGE or classify as unknown→quarantine.",
        )

    try:
        with file_lock(status_path, check_mtime=False):
            if status_path.exists():
                status = read_json(status_path)
            else:
                status = _baseline_status(lead_id)
            current = status.get("current_stage", "not_applied")

            # Idempotency — decided by Model B's own transitions (the bridge
            # cannot trust confirmation.update_status's non-discriminating
            # return value; it inspects the durable record directly).
            if any(t.get("event_id") == eid for t in status.get("transitions", [])):
                return BridgeResult("noop_duplicate", lead_id, current, current, eid)

            if current in TERMINAL_STAGES:
                return BridgeResult("noop_terminal", lead_id, current, current, eid)

            if target == current:
                return BridgeResult("noop_backward", lead_id, current, current, eid)

            inferred_skip = False
            if target in OUTCOME_STAGES:
                # rejected/withdrawn/ghosted: allowed from any non-terminal.
                pass
            else:
                cur_rank = STAGE_LADDER.get(current)
                tgt_rank = STAGE_LADDER.get(target)
                if cur_rank is None or tgt_rank is None or tgt_rank <= cur_rank:
                    return BridgeResult("noop_backward", lead_id, current, target, eid)
                inferred_skip = (tgt_rank - cur_rank) > 1

            _apply_transition_locked(
                status,
                target,
                note=f"triage:{parsed.event_type}",
                event_id=eid,
                inferred_skip=inferred_skip,
            )
            from .utils import write_json

            write_json(status_path, status)
            return BridgeResult(
                "advanced", lead_id, current, target, eid, inferred_skip
            )
    except FileLockContentionError:
        return BridgeResult("skipped_contention", lead_id, None, target, eid)
