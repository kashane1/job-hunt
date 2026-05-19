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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from .analytics import STAGE_SEQUENCE
from .confirmation import SENDER_ALLOWLIST, ParsedEmail
from .tracking import (
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
            from .utils import write_json

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


_DKIM_D_RE: Final = re.compile(
    r"dkim=pass\b[^;]*?\bheader\.d=([A-Za-z0-9.-]+)", re.IGNORECASE,
)


def dkim_pass_domain(authentication_results: str) -> str | None:
    """Return the DKIM ``header.d=`` signing domain ONLY when dkim=pass.

    confirmation._dkim_pass checks the substring; trust binding additionally
    needs the signing domain. ``From`` / display-name / body are never read.
    """
    if not authentication_results:
        return None
    m = _DKIM_D_RE.search(authentication_results)
    if not m:
        return None
    return registrable_domain(m.group(1))


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
_RECRUITER_LABEL_TO_STAGE: Final[dict[str, str]] = {
    "rejection": "rejected",
    "phone_screen": "phone_screen",
    "interview": "onsite",
    "offer": "offer",
    # assessment_request: intentionally absent — no current_stage change.
}
# Outcomes are the high-value spoofing targets: from a non-allowlisted (but
# DKIM-domain-matched) sender they quarantine for human promotion.
_OUTCOME_LABELS: Final = frozenset({"rejection", "offer"})


@dataclass(frozen=True)
class CorrelationResult:
    lead_id: str | None
    candidates: tuple[str, ...]
    decision: Literal["match", "ambiguous", "no_match", "sender_unverified"]


@dataclass(frozen=True)
class CorrelationIndex:
    by_jk: dict[str, str]              # indeed_jk -> draft_id
    by_posting_url: dict[str, str]    # posting_url -> draft_id
    by_company_domain: dict[str, tuple[str, ...]]  # eTLD+1 -> lead_ids
    company_token: dict[str, str]     # lead_id -> lowercased company core token


def _company_core_token(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    for tok in cleaned.split():
        if len(tok) >= 3 and tok not in ("the", "inc", "llc", "ltd", "corp"):
            return tok
    return ""


def build_correlation_index(data_root: Path | None = None) -> CorrelationIndex:
    """One pass over plan/lead/company records (mirrors analytics'
    load-once idiom — avoids the O(emails × drafts) re-glob)."""
    data_root = data_root or (repo_root() / "data")
    by_jk: dict[str, str] = {}
    by_url: dict[str, str] = {}
    apps = data_root / "applications"
    if apps.is_dir():
        for d in apps.iterdir():
            if not d.is_dir() or d.name in ("batches", "_suspicious"):
                continue
            pp = d / "plan.json"
            if not pp.exists():
                continue
            try:
                plan = read_json(pp)
            except Exception:
                continue
            keys = plan.get("correlation_keys", {}) or {}
            did = plan.get("draft_id", d.name)
            if keys.get("indeed_jk"):
                by_jk[keys["indeed_jk"]] = did
            if keys.get("posting_url"):
                by_url[keys["posting_url"]] = did

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
        by_jk=by_jk, by_posting_url=by_url,
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
        return BridgeResult("noop_backward", lead_id, None, None, eid)
    return _bridge_to_stage(
        lead_id=lead_id, target=target,
        note=f"triage:recruiter:{klass.label} ({klass.matched_rule})",
        event_id=eid, data_root=data_root,
    )
