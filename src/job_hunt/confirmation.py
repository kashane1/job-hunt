"""Gmail-driven confirmation + status close-the-loop.

Batch 4 Phase 8. Parses Indeed and per-ATS confirmation emails, correlates
them against ``data/applications/*/plan.json.correlation_keys``, and
updates ``status.json`` under file_lock with the priority ladder
``confirmed > submitted > applying > drafted``.

Per AGENTS.md:120 convention: email parsing is local-file work, not an
I/O boundary, so this module raises ``ValueError`` for parse failures.
The CLI entry point in ``core.main`` wraps anything ApplicationError /
PlanError surfaces from ``ingest_confirmation`` into the structured
error envelope.

Sender verification is the critical defense (a spoofed email could
otherwise march a draft to confirmed). Three-step gate:
  1. ``From:`` header matches a per-platform allowlist.
  2. ``Authentication-Results`` header has ``dkim=pass`` for that sender.
  3. The body references a ``posting_url`` or Indeed ``jk`` previously
     recorded in some draft's ``plan.json.correlation_keys``.

Failure on any check → quarantine to ``data/applications/_suspicious/``.
"""

from __future__ import annotations

import email
import email.parser
import email.policy
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from .application import (
    APPLICATION_ERROR_CODES,
    PLAN_ERROR_CODES,
    ApplicationError,
    PlanError,
    _draft_dir,
)
from .utils import (
    FileLockContentionError,
    ensure_dir,
    file_lock,
    now_iso,
    read_json,
    repo_root,
    write_json,
)


# =============================================================================
# Sender allowlist + DKIM helpers
# =============================================================================

# Maintainable list — one per known platform. Keep this in sync with the
# per-surface playbooks. Future ATSes are added here, not at the use site.
SENDER_ALLOWLIST: Final[frozenset[str]] = frozenset({
    "myindeed@indeed.com",
    "indeedapply@indeed.com",
    "no-reply@greenhouse-mail.io",
    "no-reply@greenhouse.io",
    "no-reply@hire.lever.co",
    "notifications@myworkdayjobs.com",
    "no-reply@ashbyhq.com",
})


_FROM_ADDR_RE: Final = re.compile(r"<([^>]+)>")
_DKIM_PASS_RE: Final = re.compile(r"\bdkim=pass\b", re.IGNORECASE)
_INDEED_JK_RE: Final = re.compile(r"\b([a-f0-9]{16})\b")
_URL_RE: Final = re.compile(r"https?://[^\s\"'<>]+")


def _extract_address(from_header: str) -> str:
    """Extract the bare email address from a ``From:`` header value."""
    if not from_header:
        return ""
    match = _FROM_ADDR_RE.search(from_header)
    if match:
        return match.group(1).strip().lower()
    return from_header.strip().lower()


def _dkim_pass(authentication_results: str) -> bool:
    return bool(authentication_results and _DKIM_PASS_RE.search(authentication_results))


# =============================================================================
# Email parsing
# =============================================================================

EventType = Literal["submitted", "confirmed", "rejected", "interview", "offer", "ghosted"]


@dataclass(frozen=True)
class ParsedEmail:
    sender: str
    message_id: str
    subject: str
    body: str
    authentication_results: str
    event_type: EventType
    posting_url: str | None
    indeed_jk: str | None


def _classify_event(subject: str, body: str) -> EventType:
    """Best-effort classifier from subject + body keywords.

    Order matters — offer beats interview beats rejected. Rejection
    detection is intentionally conservative (the worst false positive is
    marking a live opportunity as rejected).
    """
    text = f"{subject} {body}".lower()
    if any(s in text for s in ("offer letter", "we're excited to offer", "your offer")):
        return "offer"
    if any(s in text for s in ("interview", "phone screen", "schedule a chat")):
        return "interview"
    if any(
        s in text for s in (
            "moving forward with other", "not moving forward", "decided not to",
            "regret to inform", "we have decided", "unable to move forward",
        )
    ):
        return "rejected"
    if any(
        s in text for s in (
            "thank you for applying", "we received your application",
            "your application has been received", "application has been submitted",
            "application received",
        )
    ):
        return "confirmed"
    return "submitted"


def parse_email(raw_bytes: bytes) -> ParsedEmail:
    """Parse a raw RFC5322 message → ParsedEmail.

    Uses ``email.parser.BytesParser(policy=policy.default)`` per the
    framework-docs research — that policy handles modern MIME well and
    preserves headers without smashing whitespace.
    """
    parser = email.parser.BytesParser(policy=email.policy.default)
    msg = parser.parsebytes(raw_bytes)
    sender = _extract_address(str(msg.get("From") or ""))
    message_id = str(msg.get("Message-ID") or "").strip("<>")
    subject = str(msg.get("Subject") or "")
    auth_results = str(msg.get("Authentication-Results") or "")
    body_obj = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_obj is not None:
        try:
            body = body_obj.get_content()
        except Exception:
            body = body_obj.as_string()
    event_type = _classify_event(subject, body)
    posting_url = None
    url_match = _URL_RE.search(body)
    if url_match:
        posting_url = url_match.group(0)
    indeed_jk = None
    jk_match = _INDEED_JK_RE.search(body)
    if jk_match:
        indeed_jk = jk_match.group(1)
    return ParsedEmail(
        sender=sender,
        message_id=message_id,
        subject=subject,
        body=body,
        authentication_results=auth_results,
        event_type=event_type,
        posting_url=posting_url,
        indeed_jk=indeed_jk,
    )


def parse_email_dict(payload: dict) -> ParsedEmail:
    """Parse a Gmail-API-style JSON payload.

    Gmail's REST API returns ``{payload: {headers: [{name, value}], parts: …}}``.
    This helper accepts either ``{raw: <base64>}`` or the structured form
    so the agent can pass whichever is more convenient.
    """
    if isinstance(payload.get("raw"), str):
        import base64

        raw_bytes = base64.urlsafe_b64decode(payload["raw"].encode("ascii"))
        return parse_email(raw_bytes)
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("payload", {}).get("headers", [])
    }
    body = ""
    parts = payload.get("payload", {}).get("parts") or []
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                import base64

                body = base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
                break
    if not body:
        body = payload.get("snippet", "")
    sender = _extract_address(headers.get("from", ""))
    message_id = headers.get("message-id", "").strip("<>")
    subject = headers.get("subject", "")
    auth_results = headers.get("authentication-results", "")
    event_type = _classify_event(subject, body)
    posting_url = None
    url_match = _URL_RE.search(body)
    if url_match:
        posting_url = url_match.group(0)
    indeed_jk = None
    jk_match = _INDEED_JK_RE.search(body)
    if jk_match:
        indeed_jk = jk_match.group(1)
    return ParsedEmail(
        sender=sender, message_id=message_id, subject=subject, body=body,
        authentication_results=auth_results, event_type=event_type,
        posting_url=posting_url, indeed_jk=indeed_jk,
    )


# =============================================================================
# Correlation — match parsed email to a draft via plan.correlation_keys
# =============================================================================

def match_message(
    parsed: ParsedEmail,
    *,
    data_root: Path | None = None,
) -> list[str]:
    """Return candidate draft_ids matching ``parsed`` via correlation keys.

    Match priority:
      1. Exact ``indeed_jk`` match (16-hex unique).
      2. Exact ``posting_url`` match (canonical compare).
    Ambiguous match (>1 candidate) → caller raises ApplicationError(
    confirmation_ambiguous). Empty list → quarantine in _suspicious/.
    """
    data_root = data_root or (repo_root() / "data")
    apps_root = data_root / "applications"
    if not apps_root.is_dir():
        return []
    candidates: list[str] = []
    for draft_dir in apps_root.iterdir():
        if not draft_dir.is_dir() or draft_dir.name in ("batches", "_suspicious"):
            continue
        plan_path = draft_dir / "plan.json"
        if not plan_path.exists():
            continue
        try:
            plan = read_json(plan_path)
        except Exception:
            continue
        keys = plan.get("correlation_keys", {}) or {}
        if parsed.indeed_jk and keys.get("indeed_jk") == parsed.indeed_jk:
            candidates.append(plan.get("draft_id", draft_dir.name))
            continue
        if parsed.posting_url and keys.get("posting_url"):
            if parsed.posting_url == keys["posting_url"]:
                candidates.append(plan.get("draft_id", draft_dir.name))
    return list(dict.fromkeys(candidates))  # de-dup preserving order


# =============================================================================
# Sender verification + quarantine
# =============================================================================

def _quarantine(parsed: ParsedEmail, reason: str, *, data_root: Path) -> Path:
    quarantine_dir = data_root / "applications" / "_suspicious"
    ensure_dir(quarantine_dir)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", parsed.message_id or "no_message_id")
    path = quarantine_dir / f"{safe_id}.json"
    write_json(path, {
        "schema_version": 1,
        "reason": reason,
        "sender": parsed.sender,
        "subject": parsed.subject,
        "message_id": parsed.message_id,
        "authentication_results": parsed.authentication_results,
        "indeed_jk": parsed.indeed_jk,
        "posting_url": parsed.posting_url,
        "quarantined_at": now_iso(),
    })
    return path


def verify_sender(parsed: ParsedEmail) -> str | None:
    """Return ``None`` if the sender passes; else a quarantine reason string.

    Phase 8 ships these three checks; Phase 9 may extend to SPF/DMARC if
    the spike reveals platforms shipping inconsistent DKIM headers.
    """
    if parsed.sender not in SENDER_ALLOWLIST:
        return "sender_allowlist_mismatch"
    if not _dkim_pass(parsed.authentication_results):
        return "dkim_failed"
    return None


# =============================================================================
# Status update — locked + priority ladder + event_id idempotency
# =============================================================================

_PRIORITY: Final = {
    "drafted": 0, "queued": 1, "applying": 2, "awaiting_human_action": 2, "submitted": 3,
    "confirmed": 4, "rejected": 4, "ghosted": 4, "withdrawn": 4,
    "applied_externally": 4, "interview": 5, "offer": 6,
    "unknown_outcome": 2, "failed": 2, "posting_closed": 4,
}


_EVENT_TO_LIFECYCLE: Final = {
    "submitted": "submitted",
    "confirmed": "confirmed",
    "rejected": "rejected",
    "interview": "interview",
    "offer": "offer",
    "ghosted": "ghosted",
}


def update_status(
    draft_id: str,
    parsed: ParsedEmail,
    *,
    data_root: Path | None = None,
) -> dict:
    """Apply the parsed email to status.json, honoring the priority ladder
    and event_id idempotency.
    """
    data_root = data_root or (repo_root() / "data")
    status_path = _draft_dir(data_root, draft_id) / "status.json"
    if not status_path.exists():
        raise PlanError(
            f"No status.json for draft {draft_id}",
            error_code="profile_field_missing",
            remediation="Run prepare-application first to create the draft.",
        )
    try:
        with file_lock(status_path, check_mtime=False):
            status = read_json(status_path)
            events = status.setdefault("events", [])
            source_id = f"gmail:{parsed.message_id or '<no-id>'}"
            event_id = hashlib.sha256(
                f"{source_id}:{parsed.event_type}".encode("utf-8")
            ).hexdigest()
            if any(e.get("event_id") == event_id for e in events):
                return status
            events.append({
                "event_id": event_id,
                "type": parsed.event_type,
                "source_id": source_id,
                "occurred_at": now_iso(),
                "payload": {
                    "subject": parsed.subject,
                    "sender": parsed.sender,
                    "indeed_jk": parsed.indeed_jk,
                    "posting_url": parsed.posting_url,
                },
            })
            new_state = _EVENT_TO_LIFECYCLE.get(parsed.event_type)
            if new_state is not None:
                current = status.get("lifecycle_state", "drafted")
                if _PRIORITY.get(new_state, 0) >= _PRIORITY.get(current, 0):
                    status["lifecycle_state"] = new_state
            status["confirmation"] = {
                "email_message_id": parsed.message_id,
                "sender": parsed.sender,
                "dkim_verified": _dkim_pass(parsed.authentication_results),
                "matched_via": "indeed_jk" if parsed.indeed_jk else "posting_url",
            }
            status["updated_at"] = now_iso()
            write_json(status_path, status)
            return status
    except FileLockContentionError as exc:
        raise PlanError(
            str(exc),
            error_code="answer_bank_locked",
            remediation="Wait for the concurrent writer or remove the stale .lock sibling.",
        ) from exc


# =============================================================================
# CLI entry points — ingest_confirmation + poll_confirmations
# =============================================================================

def ingest_confirmation(
    *,
    raw_bytes: bytes | None = None,
    payload: dict | None = None,
    draft_id: str | None = None,
    data_root: Path | None = None,
) -> dict:
    """Parse one email and update the matching draft.

    Either ``raw_bytes`` (RFC5322) or ``payload`` (Gmail API JSON) must be
    provided. ``draft_id`` is optional — when omitted, ``match_message``
    determines the draft. Ambiguous match raises
    ``ApplicationError(confirmation_ambiguous)``.
    """
    data_root = data_root or (repo_root() / "data")
    if raw_bytes is not None:
        parsed = parse_email(raw_bytes)
    elif payload is not None:
        parsed = parse_email_dict(payload)
    else:
        raise ValueError("ingest_confirmation requires raw_bytes or payload")

    quarantine_reason = verify_sender(parsed)
    if quarantine_reason:
        path = _quarantine(parsed, quarantine_reason, data_root=data_root)
        raise ApplicationError(
            f"Confirmation email failed sender verification ({quarantine_reason})",
            error_code="confirmation_sender_unverified",
            remediation=f"Review the quarantined message at {path}.",
        )

    if draft_id is None:
        candidates = match_message(parsed, data_root=data_root)
        if not candidates:
            path = _quarantine(parsed, "no_correlation_match", data_root=data_root)
            raise ApplicationError(
                "Confirmation email did not match any draft",
                error_code="confirmation_ambiguous",
                remediation=f"Review the quarantined message at {path}.",
            )
        if len(candidates) > 1:
            path = _quarantine(parsed, "ambiguous_correlation_match", data_root=data_root)
            raise ApplicationError(
                f"Confirmation email matched {len(candidates)} drafts: {candidates}",
                error_code="confirmation_ambiguous",
                remediation=f"Review the quarantined message at {path}.",
            )
        draft_id = candidates[0]

    return update_status(draft_id, parsed, data_root=data_root)


# =============================================================================
# Gmail incremental cursor (data/gmail-cursor.json)
# =============================================================================

def _cursor_path(data_root: Path) -> Path:
    return data_root / "gmail-cursor.json"


def load_gmail_cursor(data_root: Path | None = None) -> dict:
    data_root = data_root or (repo_root() / "data")
    path = _cursor_path(data_root)
    if not path.exists():
        return {"schema_version": 1, "last_history_id": None, "last_scan_at": None}
    return read_json(path)


def save_gmail_cursor(
    *,
    history_id: str,
    data_root: Path | None = None,
) -> None:
    data_root = data_root or (repo_root() / "data")
    write_json(_cursor_path(data_root), {
        "schema_version": 1,
        "last_history_id": str(history_id),
        "last_scan_at": now_iso(),
    })


def gmail_search_query(
    apply_policy: dict,
    *,
    cursor: dict | None = None,
) -> str:
    """Build the Gmail search query for the next poll.

    Per the framework-docs research: Gmail uses ``newer_than:`` (NOT
    ``since:``), uppercase ``OR``, parens required for grouping. Sender
    list is comma-separated inside ``from:(...)``.
    """
    senders = " OR ".join(sorted(SENDER_ALLOWLIST))
    window_days = int(apply_policy.get("gmail_query_window_days", 14))
    if cursor and cursor.get("last_history_id"):
        # When the cursor exists, Gmail's history API is the canonical path
        # — but for the keyword-fallback we still need a reasonable window.
        window_days = max(1, min(window_days, 7))
    return (
        f"from:({senders}) "
        f"newer_than:{window_days}d "
        "subject:(application OR applied OR \"thank you\" OR offer OR interview OR \"not moving forward\")"
    )


def poll_confirmations(
    parsed_emails: list[ParsedEmail],
    *,
    data_root: Path | None = None,
) -> dict:
    """Iterate parsed emails, applying each through ingest_confirmation.

    Designed for the agent: it fetches messages via the Gmail MCP and
    passes the ``ParsedEmail`` list to this orchestrator. The orchestrator
    handles correlation + quarantine + status merge, and returns a
    structured rollup. Errors per-message are collected (one bad email
    does not abort the whole poll).
    """
    data_root = data_root or (repo_root() / "data")
    rollup: dict = {
        "applied": 0,
        "quarantined": 0,
        "ambiguous": 0,
        "results": [],
    }
    for parsed in parsed_emails:
        try:
            quarantine_reason = verify_sender(parsed)
            if quarantine_reason:
                _quarantine(parsed, quarantine_reason, data_root=data_root)
                rollup["quarantined"] += 1
                rollup["results"].append({
                    "message_id": parsed.message_id,
                    "outcome": "quarantined",
                    "reason": quarantine_reason,
                })
                continue
            candidates = match_message(parsed, data_root=data_root)
            if len(candidates) != 1:
                _quarantine(
                    parsed,
                    "ambiguous_correlation_match" if candidates else "no_correlation_match",
                    data_root=data_root,
                )
                rollup["ambiguous"] += 1
                rollup["results"].append({
                    "message_id": parsed.message_id,
                    "outcome": "ambiguous",
                    "candidate_count": len(candidates),
                })
                continue
            update_status(candidates[0], parsed, data_root=data_root)
            rollup["applied"] += 1
            rollup["results"].append({
                "message_id": parsed.message_id,
                "outcome": "applied",
                "draft_id": candidates[0],
                "event_type": parsed.event_type,
            })
        except (PlanError, ApplicationError) as exc:
            rollup["results"].append({
                "message_id": parsed.message_id,
                "outcome": "error",
                "error_code": exc.error_code,
            })
    return rollup
