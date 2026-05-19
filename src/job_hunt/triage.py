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
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from .analytics import STAGE_SEQUENCE
from .confirmation import (
    SENDER_ALLOWLIST,
    ParsedEmail,
    _dkim_pass,
    _quarantine,
    match_message,
)
from .tracking import (
    TERMINAL_STAGES,
    VALID_STAGES,
    _apply_transition_locked,
)
from .utils import (
    FileLockContentionError,
    StructuredError,
    ensure_dir,
    file_lock,
    now_iso,
    read_json,
    repo_root,
    write_json,
)

TRIAGE_ERROR_CODES: Final = frozenset({
    "triage_low_confidence",
    "triage_ambiguous_correlation",
    "triage_no_correlation",
    "triage_status_locked",
    "triage_unbridged",
    "triage_sender_unverified",
    "triage_invalid_input",
    "triage_quarantine_not_found",
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
# Outcomes are not ladder-ranked (reachable from any non-terminal stage).
# `withdrawn` is intentionally absent — no triage event/label/scan targets
# it (it is a human-only manual transition).
OUTCOME_STAGES: Final = frozenset({"rejected", "ghosted"})

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
        "noop_no_stage",
    ]
    lead_id: str | None
    from_stage: str | None
    to_stage: str | None
    event_id: str
    inferred_skip: bool = False


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


def _bridge_to_stage(
    *,
    lead_id: str,
    target: str,
    note: str,
    event_id: str,
    data_root: Path,
) -> BridgeResult:
    """Locked decision + write core shared by the confirmation-event and
    recruiter-classifier paths. The whole pre-validate-and-write is a SINGLE
    ``file_lock`` acquisition (no second lock anywhere) so a concurrent
    manual ``update-status`` cannot race the decision."""
    status_path = _status_path(data_root, lead_id)
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
            if any(t.get("event_id") == event_id for t in status.get("transitions", [])):
                return BridgeResult("noop_duplicate", lead_id, current, current, event_id)

            if current in TERMINAL_STAGES:
                return BridgeResult("noop_terminal", lead_id, current, current, event_id)

            if target == current:
                return BridgeResult("noop_backward", lead_id, current, current, event_id)

            inferred_skip = False
            if target in OUTCOME_STAGES:
                pass  # rejected/withdrawn/ghosted: allowed from any non-terminal
            else:
                cur_rank = STAGE_LADDER.get(current)
                tgt_rank = STAGE_LADDER.get(target)
                if cur_rank is None or tgt_rank is None or tgt_rank <= cur_rank:
                    return BridgeResult("noop_backward", lead_id, current, target, event_id)
                inferred_skip = (tgt_rank - cur_rank) > 1

            _apply_transition_locked(
                status, target, note=note,
                event_id=event_id, inferred_skip=inferred_skip,
            )
            write_json(status_path, status)
            return BridgeResult(
                "advanced", lead_id, current, target, event_id, inferred_skip
            )
    except FileLockContentionError:
        return BridgeResult("skipped_contention", lead_id, None, target, event_id)


def bridge_event(
    parsed: ParsedEmail,
    *,
    lead_id: str,
    data_root: Path | None = None,
) -> BridgeResult:
    """Apply a verified, correlated *confirmation* email to Model B.

    The caller has already verified + correlated the email. ``event_id``
    uses confirmation's exact formula so Model A and Model B dedup on the
    same identity.
    """
    data_root = data_root or (repo_root() / "data")
    target = _EVENT_TO_STAGE.get(parsed.event_type)
    if target is None or target not in VALID_STAGES:
        raise TriageError(
            f"Unmapped event_type {parsed.event_type!r}",
            error_code="triage_invalid_input",
            remediation="Extend _EVENT_TO_STAGE or classify as unknown→quarantine.",
        )
    return _bridge_to_stage(
        lead_id=lead_id, target=target, note=f"triage:{parsed.event_type}",
        event_id=event_id_for(parsed), data_root=data_root,
    )


# =============================================================================
# Phase 2 — redaction, DKIM-d= correlation, recruiter classifier
# =============================================================================

# Untrusted email bodies are bounded BEFORE regex (catastrophic-backtracking
# / DoS guard) and HTML-stripped so patterns stay linear-time.
# Bounded quantifiers everywhere — an unbounded `+` against a long run with
# no terminator is quadratic (a real DoS on adversarial email bodies). RFC
# 5321 caps local-part at 64 and domain at 255; phone at a sane 7..20.
_MAX_BODY_CHARS: Final = 256 * 1024
_HTML_TAG_RE: Final = re.compile(r"<[^>]{0,4096}>")
_EMAIL_RE: Final = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}"
)
_PHONE_RE: Final = re.compile(r"(?<!\w)\+?\d[\d .()-]{6,18}\d(?!\w)")
_REDACTED: Final = "[REDACTED]"


def redact_email(parsed: ParsedEmail) -> ParsedEmail:
    """Single chokepoint: strip HTML, bound size, redact emails/phones from
    ``subject`` + ``body`` BEFORE any persistence (`_suspicious/`, Model-A
    payload, logs). Frozen dataclass → ``dataclasses.replace`` (never mutate).
    """
    def scrub(text: str) -> str:
        text = (text or "")[:_MAX_BODY_CHARS]
        text = _HTML_TAG_RE.sub(" ", text)
        text = _EMAIL_RE.sub(_REDACTED, text)
        text = _PHONE_RE.sub(_REDACTED, text)
        return text

    return dataclasses.replace(
        parsed, subject=scrub(parsed.subject), body=scrub(parsed.body),
    )


# Hosts that are ATS / aggregator / social — never a company's own domain,
# so a DKIM signature from one of these proves nothing about the employer.
_NON_COMPANY_HOSTS: Final = frozenset({
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com",
    "workable.com", "myworkdayjobs.com", "workday.com", "smartrecruiters.com",
    "bamboohr.com", "indeed.com", "linkedin.com", "glassdoor.com",
    "google.com", "github.com", "notion.so", "gmail.com", "outlook.com",
})

# Pragmatic stdlib public-suffix subset (no external dep — repo is
# stdlib-only). Documented limitation: exotic multi-part suffixes fall back
# to last-two-labels, which is conservative (over-narrows, never over-trusts).
_MULTI_SUFFIXES: Final = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.jp", "co.nz", "com.br", "co.in", "com.sg", "com.mx", "co.za",
    "co.il", "com.tr", "co.kr", "org.nz", "gov.sg", "com.cn", "co.id",
    "com.hk", "com.tw", "com.ar", "co.th", "com.my", "com.ph", "ne.jp",
})


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain (eTLD+1). Equality on this value is
    what defeats lookalikes: ``stripe-careers.com`` and
    ``stripe.com.evil.net`` never reduce to ``stripe.com``."""
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) < 2:
        return host
    last2 = ".".join(parts[-2:])
    if len(parts) >= 3 and last2 in _MULTI_SUFFIXES:
        return ".".join(parts[-3:])
    return last2


# One (status, header.d=) pair per `dkim=` token. We parse EVERY dkim result,
# not just the first — an attacker appends a fake `dkim=pass header.d=victim`
# after a real `dkim=fail`, so first-match is forgeable (todo 068).
_DKIM_RESULT_RE: Final = re.compile(
    r"\bdkim=(pass|fail|none|neutral|policy|permerror|temperror)\b"
    r"(?:[^;]*?\bheader\.d=([A-Za-z0-9.-]+))?",
    re.IGNORECASE,
)


def dkim_pass_domain(authentication_results: str) -> str | None:
    """Registrable signing domain of a *passing* DKIM result, fail-closed.

    Every ``dkim=`` result in ``Authentication-Results`` is parsed. A
    registrable domain is returned only if it has a ``dkim=pass`` AND no
    non-pass ``dkim=`` result for that same registrable domain (so a forged
    ``dkim=pass header.d=victim`` appended after a real ``dkim=fail`` for
    victim cannot win). ``From`` / display-name / body are never read.

    NOTE: ``Authentication-Results`` is only trustworthy when stamped by a
    trusted inbound verifier (the Gmail-API path). Raw ``.eml`` via
    ``--inbox-file`` is author-controlled — which is why non-allowlisted
    senders never auto-advance regardless of this value (see triage_inbox).
    """
    if not authentication_results:
        return None
    passed: set[str] = set()
    failed: set[str] = set()
    for status, domain in _DKIM_RESULT_RE.findall(authentication_results):
        if not domain:
            continue
        reg = registrable_domain(domain)
        (passed if status.lower() == "pass" else failed).add(reg)
    candidates = passed - failed
    if len(candidates) != 1:
        return None  # zero, or an ambiguous/contradicted set → fail closed
    return next(iter(candidates))


RecruiterLabel = Literal[
    "rejection", "phone_screen", "interview", "assessment_request",
    "offer", "unknown",
]


@dataclass(frozen=True)
class RecruiterClass:
    label: RecruiterLabel
    matched_rule: str  # the phrase that fired — required for trust-audit


# Deterministic, auditable. Order = severity/precedence: a single email that
# says "we're moving forward — here's a coding test" is an assessment, not a
# rejection; offer beats everything. Uncertain ⇒ unknown ⇒ quarantine.
_CLASSIFIER_RULES: tuple[tuple[RecruiterLabel, tuple[str, ...]], ...] = (
    ("offer", ("offer letter", "we're excited to offer", "your offer",
               "pleased to offer", "extend you an offer")),
    ("assessment_request", ("coding challenge", "take-home", "hackerrank",
                            "codility", "online assessment", "coding test",
                            "technical assessment", "complete the assessment")),
    ("phone_screen", ("phone screen", "recruiter screen", "intro call",
                       "initial call", "screening call", "quick chat with")),
    ("interview", ("interview", "schedule a chat", "onsite", "next round",
                   "meet the team", "technical interview")),
    ("rejection", ("not moving forward", "moving forward with other",
                   "decided not to", "regret to inform", "we have decided",
                   "unable to move forward", "will not be proceeding",
                   "pursue other candidates")),
)


def classify_recruiter_email(parsed: ParsedEmail) -> RecruiterClass:
    """Pure, deterministic classification over redacted text. No I/O, no LLM
    (asserted by a test) — the testable seam for the truth-table."""
    text = f"{parsed.subject}\n{parsed.body}".lower()
    for label, phrases in _CLASSIFIER_RULES:
        for phrase in phrases:
            if phrase in text:
                return RecruiterClass(label, phrase)
    return RecruiterClass("unknown", "")


# A recruiter label that is NOT a forward funnel step has no Model-B stage.
# Used by bridge_recruiter (the promotion primitive for the quarantine
# review path, todo 070).
_RECRUITER_LABEL_TO_STAGE: Final[dict[str, str]] = {
    "rejection": "rejected",
    "phone_screen": "phone_screen",
    "interview": "onsite",
    "offer": "offer",
    # assessment_request: intentionally absent — no current_stage change.
}


@dataclass(frozen=True)
class CorrelationResult:
    lead_id: str | None
    candidates: tuple[str, ...]
    decision: Literal["match", "ambiguous", "no_match", "sender_unverified"]


@dataclass(frozen=True)
class CorrelationIndex:
    by_company_domain: dict[str, tuple[str, ...]]  # eTLD+1 -> lead_ids
    company_token: dict[str, str]     # lead_id -> lowercased company core token


def _company_core_token(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    for tok in cleaned.split():
        if len(tok) >= 3 and tok not in ("the", "inc", "llc", "ltd", "corp"):
            return tok
    return ""


def build_correlation_index(data_root: Path | None = None) -> CorrelationIndex:
    """One pass over lead/company records (mirrors analytics' load-once
    idiom — avoids an O(emails × leads) re-glob in the review path)."""
    data_root = data_root or (repo_root() / "data")
    companies: dict[str, str] = {}  # company_id -> registrable domain
    cdir = data_root / "companies"
    if cdir.is_dir():
        for cp in cdir.glob("*.json"):
            try:
                c = read_json(cp)
            except Exception:
                continue
            cid = c.get("company_id", cp.stem)
            for url in c.get("source_urls", []) or []:
                host = re.sub(r"^https?://", "", str(url)).split("/")[0]
                dom = registrable_domain(host)
                if dom and dom not in _NON_COMPANY_HOSTS:
                    companies[cid] = dom
                    break

    by_dom: dict[str, list[str]] = {}
    tokens: dict[str, str] = {}
    ldir = data_root / "leads"
    if ldir.is_dir():
        for lp in ldir.glob("*.json"):
            try:
                lead = read_json(lp)
            except Exception:
                continue
            lid = lead.get("lead_id", lp.stem)
            tokens[lid] = _company_core_token(lead.get("company", ""))
            crid = lead.get("company_research_id")
            dom = companies.get(crid) if crid else None
            if dom:
                by_dom.setdefault(dom, []).append(lid)
    return CorrelationIndex(
        by_company_domain={k: tuple(v) for k, v in by_dom.items()},
        company_token=tokens,
    )


def correlate_recruiter(
    parsed: ParsedEmail,
    index: CorrelationIndex,
) -> CorrelationResult:
    """Resolve a recruiter email to exactly one lead, fail-closed.

    Trust binding: the DKIM ``header.d=`` registrable domain (NOT From, NOT
    body) must equal the registrable domain stored for the lead's company,
    AND the company core token must appear in subject/body. Equality on the
    registrable domain rejects lookalikes inherently. 0 / ≥2 ⇒ no auto path.
    """
    sender_dom = dkim_pass_domain(parsed.authentication_results)
    if sender_dom is None:
        return CorrelationResult(None, (), "sender_unverified")
    leads = index.by_company_domain.get(sender_dom, ())
    text = f"{parsed.subject}\n{parsed.body}".lower()
    confirmed = tuple(
        lid for lid in leads
        if index.company_token.get(lid) and index.company_token[lid] in text
    )
    if len(confirmed) == 1:
        return CorrelationResult(confirmed[0], confirmed, "match")
    if len(confirmed) > 1:
        return CorrelationResult(None, confirmed, "ambiguous")
    return CorrelationResult(None, (), "no_match")


def bridge_recruiter(
    parsed: ParsedEmail,
    klass: RecruiterClass,
    *,
    lead_id: str,
    data_root: Path | None = None,
) -> BridgeResult:
    """Bridge a classified recruiter email to Model B. ``assessment_request``
    and ``unknown`` have no stage → no transition (caller records an event /
    quarantines)."""
    data_root = data_root or (repo_root() / "data")
    target = _RECRUITER_LABEL_TO_STAGE.get(klass.label)
    eid = hashlib.sha256(
        f"gmail:{parsed.message_id or '<no-id>'}:recruiter:{klass.label}".encode()
    ).hexdigest()
    if target is None:
        return BridgeResult("noop_no_stage", lead_id, None, None, eid)
    return _bridge_to_stage(
        lead_id=lead_id, target=target,
        note=f"triage:recruiter:{klass.label} ({klass.matched_rule})",
        event_id=eid, data_root=data_root,
    )


# =============================================================================
# Phase 3 — inbox orchestrator, ghost-timeout scan
# =============================================================================

GHOST_DAYS_DEFAULT: Final = 21


def _lead_id_for_draft(data_root: Path, draft_id: str) -> str | None:
    pp = data_root / "applications" / draft_id / "plan.json"
    if not pp.exists():
        return None
    try:
        return read_json(pp).get("lead_id")
    except Exception:
        return None


def triage_inbox(
    parsed_emails: list[ParsedEmail],
    *,
    data_root: Path | None = None,
) -> dict:
    """Classify + bridge a batch of inbound emails. Redaction happens FIRST
    so nothing unredacted is ever persisted/logged.

    Trust split (todo 068): ``Authentication-Results`` is only trustworthy
    when stamped by a trusted inbound verifier. **Only allowlisted ATS
    senders auto-advance** — via the confirmation-event path so Model A/B
    dedup on the same ``event_id``. Every non-allowlisted email is
    quarantined for human promotion (todo 070's triage-review), regardless
    of DKIM-``d=`` match or classified label, because the header is
    author-controlled on the raw-``.eml`` path and a forged ``dkim=pass
    header.d=victim`` would otherwise march an unrelated lead forward.
    """
    data_root = data_root or (repo_root() / "data")
    index = build_correlation_index(data_root)
    rollup: dict = {"advanced": 0, "quarantined": 0, "noop": 0, "results": []}

    for raw in parsed_emails:
        parsed = redact_email(raw)
        allowlisted = raw.sender in SENDER_ALLOWLIST and _dkim_pass(
            raw.authentication_results
        )

        if allowlisted:
            drafts = match_message(raw, data_root=data_root)
            lead_id = (
                _lead_id_for_draft(data_root, drafts[0])
                if len(drafts) == 1 else None
            )
            if lead_id is None:
                _quarantine(parsed, "triage_allowlisted_no_single_draft",
                            data_root=data_root)
                rollup["quarantined"] += 1
                rollup["results"].append({
                    "message_id": parsed.message_id,
                    "outcome": "quarantined",
                    "reason": "allowlisted_no_single_draft",
                })
                continue
            # Confirmation-event path: bridge_event uses raw.event_type and
            # the SAME event_id formula as confirmation.update_status, so
            # Model A and Model B stay idempotent against each other.
            result = bridge_event(raw, lead_id=lead_id, data_root=data_root)
            bucket = "advanced" if result.outcome == "advanced" else "noop"
            rollup[bucket] += 1
            rollup["results"].append({
                "message_id": parsed.message_id,
                "outcome": result.outcome,
                "lead_id": lead_id,
                "event_type": raw.event_type,
                "to_stage": result.to_stage,
            })
            continue

        # Non-allowlisted → NEVER auto-advance. Correlate/classify only to
        # annotate the quarantine for the human/agent review path (todo 070).
        cor = correlate_recruiter(raw, index)
        klass = classify_recruiter_email(parsed)
        _quarantine(parsed, "triage_non_allowlisted_needs_review",
                    data_root=data_root)
        rollup["quarantined"] += 1
        rollup["results"].append({
            "message_id": parsed.message_id,
            "outcome": "quarantined",
            "reason": "non_allowlisted",
            "label": klass.label,
            "correlation": cor.decision,
            "lead_id": cor.lead_id,
        })
    return rollup


def _has_recent_model_a_signal(
    data_root: Path, lead_id: str, cutoff_iso: str
) -> bool:
    """True if any draft for this lead has a Model-A event newer than cutoff
    — a real inbound signal must never be overridden by a ghost timeout."""
    apps = data_root / "applications"
    if not apps.is_dir():
        return False
    for d in apps.iterdir():
        if not d.is_dir() or d.name in ("batches", "_suspicious"):
            continue
        if _lead_id_for_draft(data_root, d.name) != lead_id:
            continue
        sp = d / "status.json"
        if not sp.exists():
            continue
        try:
            for ev in read_json(sp).get("events", []):
                if str(ev.get("occurred_at", "")) > cutoff_iso:
                    return True
        except Exception:
            continue
    return False


def scan_ghost_timeouts(
    *,
    data_root: Path | None = None,
    days: int = GHOST_DAYS_DEFAULT,
    dry_run: bool = False,
) -> list[dict]:
    """Time-based (NOT an inbound event): leads stuck in a non-terminal,
    non-`not_applied` stage with no activity for ``days`` → ``ghosted``.

    Idempotency is STATE-based: if the most recent transition is already
    ``ghosted`` it is skipped (a re-quiet period after reactivation gets a
    fresh event_id tied to the last-activity timestamp). Never overrides a
    newer Model-A signal.
    """
    days = max(7, int(days))  # tighten-only floor
    data_root = data_root or (repo_root() / "data")
    cutoff = (datetime.now(UTC) - timedelta(days=days))
    cutoff_iso = cutoff.replace(microsecond=0).isoformat()
    apps = data_root / "applications"
    out: list[dict] = []
    if not apps.is_dir():
        return out
    for sp in sorted(apps.glob("*-status.json")):
        try:
            status = read_json(sp)
        except Exception:
            continue
        stage = status.get("current_stage", "not_applied")
        if stage in TERMINAL_STAGES or stage in ("ghosted", "not_applied"):
            continue
        transitions = status.get("transitions", [])
        if transitions and transitions[-1].get("to_stage") == "ghosted":
            continue  # state-based idempotency
        last_ts = (transitions[-1].get("timestamp", "") if transitions
                   else status.get("updated_at", ""))
        if last_ts and last_ts > cutoff_iso:
            continue  # still fresh
        lead_id = status.get("lead_id", sp.stem.replace("-status", ""))
        if _has_recent_model_a_signal(data_root, lead_id, cutoff_iso):
            continue
        if dry_run:
            out.append({"lead_id": lead_id, "outcome": "would_ghost",
                        "from_stage": stage})
            continue
        eid = hashlib.sha256(
            f"ghost:{lead_id}:{last_ts}".encode()
        ).hexdigest()
        r = _bridge_to_stage(
            lead_id=lead_id, target="ghosted",
            note=f"triage:ghost_timeout({days}d)",
            event_id=eid, data_root=data_root,
        )
        out.append({"lead_id": lead_id, "outcome": r.outcome,
                    "from_stage": r.from_stage})
    return out


# =============================================================================
# Phase 4 (todo 070) — quarantine review triad: list / promote / dismiss
#
# `confirmation._quarantine` stores sender/subject/message_id/auth-results but
# NO lead_id and NO proposed stage (it quarantined *because* correlation was
# not confident). This surface re-runs the pure `correlate_recruiter` +
# `classify_recruiter_email` over the stored fields to PROPOSE
# `{lead_id, to_stage, matched_rule}`; it applies only under an explicit
# `confirm` (propose-by-default — outcomes are never silently bridged, per
# AGENTS.md anti-spoof), then GCs the quarantine file so `check-integrity`
# stops counting it (the monotonic-growth bug). Mirrors discovery's
# `review-list/-promote/-dismiss` triad for agent-native parity.
# =============================================================================

# Quarantine filenames are `<safe_message_id>.json`, where safe_message_id is
# this exact substitution (kept byte-identical to confirmation._quarantine so
# a stored file is always addressable by its original Message-ID). `/` is in
# the negated class → it becomes `_`, so a Message-ID can never introduce a
# path separator; the resolved-parent fence below is defense-in-depth.
_QUARANTINE_SAFE_RE: Final = re.compile(r"[^A-Za-z0-9_.-]")

# Dotted .jsonl extension → NOT matched by check-integrity's
# `_suspicious/*.json` glob, so the durable audit trail never re-inflates
# `quarantined_confirmations` after an entry is resolved.
_QUARANTINE_AUDIT_NAME: Final = ".audit.jsonl"


def _suspicious_dir(data_root: Path) -> Path:
    return data_root / "applications" / "_suspicious"


def _quarantine_path(data_root: Path, message_id: str) -> Path:
    """Resolve a quarantine file from a (untrusted) Message-ID, fail-closed
    against path traversal — the same sanitization confirmation._quarantine
    used to write it, plus a resolved-parent containment check."""
    qdir = _suspicious_dir(data_root)
    safe = _QUARANTINE_SAFE_RE.sub("_", message_id or "no_message_id")
    path = (qdir / f"{safe}.json").resolve()
    if path.parent != qdir.resolve():
        raise TriageError(
            f"Refusing out-of-quarantine path for message_id {message_id!r}",
            error_code="triage_invalid_input",
            remediation="message_id must address a file inside _suspicious/.",
        )
    return path


def _parsed_from_quarantine(q: dict) -> ParsedEmail:
    """Reconstruct a ParsedEmail from stored quarantine fields. ``body`` was
    never persisted (privacy) → empty; re-redact the subject defensively
    (confirmation-path quarantines are not pre-redacted)."""
    return redact_email(ParsedEmail(
        sender=str(q.get("sender", "")),
        message_id=str(q.get("message_id", "")),
        subject=str(q.get("subject", "")),
        body="",
        authentication_results=str(q.get("authentication_results", "")),
        event_type="submitted",
        posting_url=q.get("posting_url"),
        indeed_jk=q.get("indeed_jk"),
    ))


def _derive_proposal(
    q: dict, index: CorrelationIndex
) -> dict:
    """Best-effort {lead_id, to_stage, label, matched_rule, correlation}
    re-derived from the stored (subject-only) fields. ``derivable`` is True
    only when BOTH a lead and a stage resolve without human override."""
    parsed = _parsed_from_quarantine(q)
    cor = correlate_recruiter(parsed, index)
    klass = classify_recruiter_email(parsed)
    to_stage = _RECRUITER_LABEL_TO_STAGE.get(klass.label)
    return {
        "lead_id": cor.lead_id,
        "to_stage": to_stage,
        "label": klass.label,
        "matched_rule": klass.matched_rule,
        "correlation": cor.decision,
        "derivable": cor.lead_id is not None and to_stage is not None,
    }


def list_triage_quarantine(data_root: Path | None = None) -> list[dict]:
    """Enumerate `_suspicious/` triage entries with a re-derived promotion
    proposal each (the agent/human reads this to decide promote vs dismiss).
    Subject/body are intentionally NOT echoed (PII hygiene — `matched_rule`
    is a fixed classifier phrase, safe to surface)."""
    data_root = data_root or (repo_root() / "data")
    qdir = _suspicious_dir(data_root)
    out: list[dict] = []
    if not qdir.is_dir():
        return out
    index = build_correlation_index(data_root)
    for sp in sorted(qdir.glob("*.json")):
        try:
            q = read_json(sp)
        except Exception:
            continue
        out.append({
            "message_id": q.get("message_id", sp.stem),
            "reason": q.get("reason", ""),
            "sender": q.get("sender", ""),
            "quarantined_at": q.get("quarantined_at", ""),
            "proposal": _derive_proposal(q, index),
        })
    return out


def _append_quarantine_audit(data_root: Path, record: dict) -> None:
    qdir = _suspicious_dir(data_root)
    ensure_dir(qdir)
    audit = qdir / _QUARANTINE_AUDIT_NAME
    line = json.dumps({**record, "ts": now_iso()}, sort_keys=True)
    with file_lock(audit, check_mtime=False):
        with audit.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def promote_triage_quarantine(
    data_root: Path,
    message_id: str,
    *,
    lead_id_override: str | None = None,
    stage_override: str | None = None,
    confirm: bool = False,
) -> dict:
    """Propose (default) or, with ``confirm``, apply a quarantined outcome.

    Re-derives `{lead_id, to_stage}` from the stored fields. Without
    ``confirm`` this is ZERO-write: it returns the proposal so the
    agent/human can inspect it (parity with `triage-inbox --dry-run`). With
    ``confirm`` it requires a resolved lead AND stage (caller supplies
    `--lead`/`--stage` when re-derivation is not confident), bridges Model B
    via the shared locked `_bridge_to_stage`, deletes the quarantine file
    (so `check-integrity` stops counting it), and writes an audit line.
    """
    path = _quarantine_path(data_root, message_id)
    if not path.exists():
        raise TriageError(
            f"No quarantine entry for message_id {message_id!r}",
            error_code="triage_quarantine_not_found",
            remediation="Run triage-review-list to see resolvable entries.",
        )
    q = read_json(path)
    index = build_correlation_index(data_root)
    proposal = _derive_proposal(q, index)

    resolved_lead = lead_id_override or proposal["lead_id"]
    resolved_stage = stage_override or proposal["to_stage"]

    if not confirm:
        return {
            "status": "proposed",
            "applied": False,
            "message_id": q.get("message_id", message_id),
            "proposal": proposal,
            "resolved_lead_id": resolved_lead,
            "resolved_stage": resolved_stage,
        }

    if resolved_lead is None:
        raise TriageError(
            f"Cannot resolve a lead for message_id {message_id!r} "
            f"(correlation={proposal['correlation']})",
            error_code="triage_no_correlation",
            remediation="Re-run with --lead <lead_id> after verifying the email.",
        )
    if resolved_stage is None:
        raise TriageError(
            f"Cannot resolve a stage for message_id {message_id!r} "
            f"(label={proposal['label']})",
            error_code="triage_low_confidence",
            remediation="Re-run with --stage <stage> after verifying the email.",
        )
    if resolved_stage not in VALID_STAGES:
        raise TriageError(
            f"Invalid stage {resolved_stage!r}",
            error_code="triage_invalid_input",
            remediation=f"--stage must be one of {sorted(VALID_STAGES)}.",
        )

    # Deterministic event_id with a distinct `review_promote` discriminator so
    # re-promoting is idempotent AND it never dedups against a later genuine
    # allowlisted confirmation for the same lead.
    eid = hashlib.sha256(
        f"gmail:{q.get('message_id') or '<no-id>'}:review_promote:{resolved_stage}".encode()
    ).hexdigest()
    note_src = proposal["matched_rule"] or stage_override or "manual"
    result = _bridge_to_stage(
        lead_id=resolved_lead, target=resolved_stage,
        note=f"triage-review-promote:{proposal['label']}({note_src})",
        event_id=eid, data_root=data_root,
    )
    # Contention is the one non-terminal outcome — keep the file so a retry
    # can still resolve it. Everything else (advanced / duplicate / terminal /
    # backward) is a decided state: GC the file + record the audit trail.
    if result.outcome != "skipped_contention":
        path.unlink(missing_ok=True)
        _append_quarantine_audit(data_root, {
            "action": "promote",
            "message_id": q.get("message_id", message_id),
            "lead_id": resolved_lead,
            "to_stage": resolved_stage,
            "outcome": result.outcome,
            "label": proposal["label"],
            "overridden": bool(lead_id_override or stage_override),
        })
    return {
        "status": "ok",
        "applied": result.outcome != "skipped_contention",
        "message_id": q.get("message_id", message_id),
        "outcome": result.outcome,
        "lead_id": resolved_lead,
        "from_stage": result.from_stage,
        "to_stage": resolved_stage,
        "event_id": eid,
        "inferred_skip": result.inferred_skip,
    }


def dismiss_triage_quarantine(
    data_root: Path,
    message_id: str,
    *,
    reason: str,
) -> dict:
    """Discard a quarantined entry with a mandatory audit reason. Deletes the
    file (so `check-integrity` stops counting it) and appends the reason to
    the audit log — discarding an outcome is a trust decision and must be
    explainable after the fact (stricter than discovery's optional reason)."""
    if not (reason or "").strip():
        raise TriageError(
            "Dismissing a quarantined outcome requires a --reason",
            error_code="triage_invalid_input",
            remediation="Re-run with --reason '<why this is not a real outcome>'.",
        )
    path = _quarantine_path(data_root, message_id)
    if not path.exists():
        raise TriageError(
            f"No quarantine entry for message_id {message_id!r}",
            error_code="triage_quarantine_not_found",
            remediation="Run triage-review-list to see resolvable entries.",
        )
    q = read_json(path)
    path.unlink(missing_ok=True)
    _append_quarantine_audit(data_root, {
        "action": "dismiss",
        "message_id": q.get("message_id", message_id),
        "reason": reason.strip(),
    })
    return {
        "status": "ok",
        "dismissed": True,
        "message_id": q.get("message_id", message_id),
    }
