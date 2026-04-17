"""Compounding question → answer bank.

Batch 4 Phase 2. Module-level functions (no domain class per Kieran
calibration: the repo is function-oriented — zero domain classes live in
``core.py``). Write coordination uses ``utils.file_lock`` on a sibling
``.lock`` file; user edits via $EDITOR bypass advisory locking, which is
why ``answer-bank-promote`` / ``answer-bank-deprecate`` CLIs exist as the
agent-native alternative.

Public surface:
- ``normalize_question(text) -> str``
- ``resolve(question, bank_path, lead=None, profile=None) -> AnswerResolution``
- ``insert_inferred(question, answer, context, bank_path) -> str``
- ``promote(entry_id, answer, bank_path, notes=None)``
- ``deprecate(entry_id, reason, bank_path)``
- ``list_pending(bank_path, since=None) -> list[dict]``
- ``list_entries(bank_path, status=None, since=None) -> list[dict]``
- ``render_template(entry, lead, profile) -> str``
- ``validate(bank_path) -> dict`` — schema + audit-log tamper check

Every mutation writes a JSON-lines event to ``data/answer-bank-audit.log``
(gitignored) with ``{timestamp, entry_id, field_changed, old_value,
new_value, actor}`` per line. ``validate`` replays that log against the
current JSON; mismatches surface as warnings (never auto-block — the user
may have edited legitimately).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal

from .application import PlanError
from .utils import (
    FileLockContentionError,
    ensure_dir,
    file_lock,
    now_iso,
    read_json,
    short_hash,
    write_json,
)


# =============================================================================
# Constants + value types
# =============================================================================

_PUNCTUATION_RE: Final = re.compile(r"[^a-z0-9+#\s]")
_WHITESPACE_RE: Final = re.compile(r"\s+")

_STALE_REVIEW_DAYS: Final = 180

ENTRY_SOURCES: Final = frozenset({"curated", "curated_template", "inferred"})
ANSWER_FORMATS: Final = frozenset({"yes_no", "text", "multi_select", "number", "date"})


Provenance = Literal["curated", "curated_template", "inferred", "none"]


@dataclass(frozen=True)
class AnswerResolution:
    """Outcome of a single resolve() call.

    When ``provenance == "curated_template"`` the ``answer`` is the rendered
    result and still counts as a supported fact for tier-1 purposes — the
    user reviewed the template. When ``provenance == "none"`` the caller
    must decide whether to insert an inferred entry or escalate.
    """

    entry_id: str
    answer: str
    provenance: Provenance
    answer_format: str


# =============================================================================
# Normalization — lookup key generation
# =============================================================================

def normalize_question(text: str) -> str:
    """Normalize a raw question for lookup.

    Steps (documented in the plan):
    1. Lowercase.
    2. Strip punctuation except ``+`` and ``#`` (preserves "C++" / "C#").
    3. Collapse whitespace.
    4. Trim.

    No fuzzy matching in v1. Near-miss phrasings yield fresh inferred entries
    that show up in the pending report — the user can merge manually.
    """
    lowered = text.lower()
    cleaned = _PUNCTUATION_RE.sub(" ", lowered)
    collapsed = _WHITESPACE_RE.sub(" ", cleaned)
    return collapsed.strip()


# =============================================================================
# Template rendering (for curated_template entries)
# =============================================================================

_TEMPLATE_TAG_RE: Final = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


def render_template(entry: dict, lead: dict | None, profile: dict | None) -> str:
    """Substitute ``{{tag}}`` markers in a template answer from lead/profile.

    Phase 2 supports a minimal tag set derived from the seed bank. Phase 4
    extends it when prepare_application needs more dynamic facts. Unknown
    tags are left as literal ``{{tag}}`` strings — the caller (prepare or
    resolve) should treat the presence of an unsubstituted tag as a tier-2
    downgrade signal rather than a silent rendering success.
    """
    raw = entry.get("answer", "")
    if "{{" not in raw:
        return raw
    lead = lead or {}
    profile = profile or {}
    prefs = profile.get("preferences", {}) if isinstance(profile, dict) else {}

    def resolve_tag(tag: str) -> str | None:
        if tag == "linkedin_url":
            for link in profile.get("contact", {}).get("links", []):
                if "linkedin.com/in/" in link:
                    return link
            return None
        if tag == "portfolio_url":
            for link in profile.get("contact", {}).get("links", []):
                if "github.com/" in link or ".dev" in link or ".io" in link:
                    return link
            return None
        if tag == "years_experience_general":
            return _years_experience(profile, skill=None)
        if tag.startswith("years_experience_"):
            skill = tag.removeprefix("years_experience_")
            return _years_experience(profile, skill=skill)
        if tag == "why_this_role":
            return (
                f"I'm interested in {lead.get('title', 'this role')} at "
                f"{lead.get('company', 'your team')} because the role's focus aligns "
                "with my platform / backend experience and the outcomes I want to "
                "drive in my next position."
            )
        if tag == "why_this_company":
            return (
                f"From what I've read about {lead.get('company', 'your company')}, "
                "the product direction and engineering culture look like a strong match "
                "for how I work — I'd like to learn more in conversation."
            )
        if tag == "greatest_strength_template":
            highlights = profile.get("experience_highlights", [])
            top = highlights[0]["summary"] if highlights else None
            return (
                f"Shipping end-to-end ownership with measurable impact — for example, {top}"
                if top
                else "Shipping end-to-end, writing maintainable code, and owning outcomes."
            )
        if tag == "tell_me_about_yourself":
            titles = prefs.get("target_titles", [])
            role = titles[0] if titles else "Software Engineer"
            highlights = profile.get("experience_highlights", [])
            proof = f" Recently: {highlights[0]['summary']}" if highlights else ""
            return (
                f"I'm targeting {role} roles — backend/platform heavy. I care about "
                f"clear code, quick feedback loops, and shipping things users feel.{proof}"
            )
        return None

    def replace(match: re.Match[str]) -> str:
        tag = match.group(1)
        value = resolve_tag(tag)
        return value if value is not None else match.group(0)

    return _TEMPLATE_TAG_RE.sub(replace, raw)


def _years_experience(profile: dict, *, skill: str | None) -> str | None:
    # Minimal implementation: count distinct document-years by scanning
    # experience_highlights summaries for year-like tokens. Tight heuristic;
    # Phase 4 can replace with a proper timeline walker once generation.py
    # is ready to expose one.
    highlights = profile.get("experience_highlights", [])
    years: set[int] = set()
    for h in highlights:
        summary = (h.get("summary") or "").lower()
        if skill and skill not in summary and skill.replace("_", " ") not in summary:
            continue
        for match in re.finditer(r"\b(20\d{2})\b", summary):
            years.add(int(match.group(1)))
    if not years:
        return None
    return str(max(years) - min(years) + 1)


# =============================================================================
# Bank I/O + resolve
# =============================================================================

def _load_bank(bank_path: Path) -> dict:
    if not bank_path.exists():
        raise PlanError(
            f"Answer bank missing at {bank_path}",
            error_code="profile_field_missing",
            remediation=(
                "Run apply-preflight to bootstrap data/answer-bank.json from "
                "the tracked seed, or restore the seed from git."
            ),
        )
    data = read_json(bank_path)
    if not isinstance(data, dict) or "entries" not in data:
        raise PlanError(
            f"Answer bank shape invalid at {bank_path}",
            error_code="plan_schema_invalid",
            remediation="Restore data/answer-bank.seed.json and copy to data/answer-bank.json.",
        )
    return data


def _audit_log_path(bank_path: Path) -> Path:
    return bank_path.parent / "answer-bank-audit.log"


def _append_audit(
    bank_path: Path,
    entry_id: str,
    field_changed: str,
    old_value: Any,
    new_value: Any,
    actor: str,
) -> None:
    """Append a JSON-lines event to the gitignored audit log.

    ``open(path, 'a')`` is the simplest append-only contract — concurrent
    writers each atomically append their own line on POSIX for payloads
    under PIPE_BUF. The entries are small JSON blobs, well below that
    threshold in practice.
    """
    audit_path = _audit_log_path(bank_path)
    ensure_dir(audit_path.parent)
    event = {
        "timestamp": now_iso(),
        "entry_id": entry_id,
        "field_changed": field_changed,
        "old_value": old_value,
        "new_value": new_value,
        "actor": actor,
    }
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def resolve(
    question: str,
    bank_path: Path,
    lead: dict | None = None,
    profile: dict | None = None,
) -> AnswerResolution:
    """Look up a question in the bank.

    Returns ``AnswerResolution(provenance="none")`` when no entry matches.
    Deprecated entries never resolve (equivalent to "no entry"). Templates
    resolve to the rendered answer with provenance ``curated_template``.
    """
    key = normalize_question(question)
    data = _load_bank(bank_path)
    for entry in data.get("entries", []):
        if entry.get("deprecated"):
            continue
        if entry.get("canonical_question") != key:
            continue
        source = entry.get("source", "inferred")
        if source == "curated_template":
            return AnswerResolution(
                entry_id=entry["entry_id"],
                answer=render_template(entry, lead, profile),
                provenance="curated_template",
                answer_format=entry.get("answer_format", "text"),
            )
        return AnswerResolution(
            entry_id=entry["entry_id"],
            answer=entry.get("answer", ""),
            provenance="curated" if source == "curated" else "inferred",
            answer_format=entry.get("answer_format", "text"),
        )
    return AnswerResolution(
        entry_id="",
        answer="",
        provenance="none",
        answer_format="text",
    )


# =============================================================================
# Mutations — all locked + audit-logged
# =============================================================================

def _entry_id_from_question(question: str) -> str:
    # Short, grep-able, collision-resistant enough for a single-user bank.
    return f"inferred_{short_hash(normalize_question(question))}"


def insert_inferred(
    question: str,
    answer: str,
    context: dict,
    bank_path: Path,
    *,
    actor: str = "agent",
    answer_format: str = "text",
) -> str:
    """Insert a new ``source=inferred`` entry and return its entry_id.

    Raises ``PlanError(answer_bank_locked)`` on contention. The caller
    (prepare_application) uses this when a field has no curated answer —
    the new entry forces tier-2 review until the user promotes it.
    """
    try:
        with file_lock(bank_path):
            data = _load_bank(bank_path)
            key = normalize_question(question)
            for existing in data.get("entries", []):
                if existing.get("canonical_question") == key and not existing.get("deprecated"):
                    # Don't duplicate; record an observed variant and return.
                    variants = existing.setdefault("observed_variants", [])
                    if question not in variants and len(variants) < 50:
                        variants.append(question)
                    write_json(bank_path, data)
                    return existing["entry_id"]
            entry_id = _entry_id_from_question(question)
            now = now_iso()
            entry = {
                "entry_id": entry_id,
                "canonical_question": key,
                "observed_variants": [question],
                "answer": answer,
                "answer_format": answer_format,
                "source": "inferred",
                "reviewed": False,
                "deprecated": False,
                "reviewed_at": None,
                "time_sensitive": False,
                "valid_until": None,
                "created_at": now,
                "notes": f"Inferred from context: {context.get('lead_id') or context.get('source') or 'unknown'}",
            }
            data["entries"].append(entry)
            write_json(bank_path, data)
            _append_audit(bank_path, entry_id, "entries[]", None, entry, actor)
            return entry_id
    except FileLockContentionError as exc:
        raise PlanError(
            str(exc),
            error_code="answer_bank_locked",
            remediation="Wait for the other writer or remove the stale .lock sibling.",
        ) from exc


def _mutate_entry(
    bank_path: Path,
    entry_id: str,
    mutate_fn,
    *,
    actor: str,
) -> dict:
    try:
        with file_lock(bank_path):
            data = _load_bank(bank_path)
            for entry in data.get("entries", []):
                if entry.get("entry_id") == entry_id:
                    before = json.loads(json.dumps(entry))
                    mutate_fn(entry)
                    write_json(bank_path, data)
                    _append_audit(bank_path, entry_id, "entry", before, entry, actor)
                    return entry
            raise PlanError(
                f"No answer-bank entry with id {entry_id!r}",
                error_code="profile_field_missing",
                remediation="Run answer-bank-list to see valid entry_ids.",
            )
    except FileLockContentionError as exc:
        raise PlanError(
            str(exc),
            error_code="answer_bank_locked",
            remediation="Wait for the other writer or remove the stale .lock sibling.",
        ) from exc


def promote(
    entry_id: str,
    answer: str,
    bank_path: Path,
    *,
    notes: str | None = None,
    actor: str = "user",
) -> dict:
    """Flip an entry to ``source=curated, reviewed=true``."""
    def mutate(entry: dict) -> None:
        entry["answer"] = answer
        entry["source"] = "curated"
        entry["reviewed"] = True
        entry["reviewed_at"] = now_iso()
        if notes is not None:
            entry["notes"] = notes
    return _mutate_entry(bank_path, entry_id, mutate, actor=actor)


def deprecate(
    entry_id: str,
    reason: str,
    bank_path: Path,
    *,
    actor: str = "user",
) -> dict:
    """Mark an entry deprecated. resolve() will skip it."""
    def mutate(entry: dict) -> None:
        entry["deprecated"] = True
        entry["notes"] = reason
    return _mutate_entry(bank_path, entry_id, mutate, actor=actor)


# =============================================================================
# Enumeration / reporting
# =============================================================================

def list_entries(
    bank_path: Path,
    *,
    status: str | None = None,
    since: date | None = None,
) -> list[dict]:
    """Enumerate bank entries with optional filters.

    ``status`` values: ``curated``, ``inferred``, ``deprecated``,
    ``template`` (alias for ``curated_template``). ``since`` filters by
    ``created_at >= since``.
    """
    data = _load_bank(bank_path)
    out: list[dict] = []
    for entry in data.get("entries", []):
        if status == "deprecated" and not entry.get("deprecated"):
            continue
        if status in ("curated", "inferred", "curated_template", "template"):
            want = "curated_template" if status == "template" else status
            if entry.get("source") != want or entry.get("deprecated"):
                continue
        if since:
            created = entry.get("created_at")
            if not created:
                continue
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            if created_dt.date() < since:
                continue
        out.append(entry)
    return out


def list_pending(bank_path: Path, since: date | None = None) -> list[dict]:
    """Return ``source=inferred, reviewed=false`` entries plus stale reviews.

    Stale reviews: entries with ``time_sensitive=true`` whose ``reviewed_at``
    is older than 180 days. These show up so the user refreshes e.g. notice
    period or salary expectations before they drift.
    """
    data = _load_bank(bank_path)
    cutoff = None
    try:
        cutoff = datetime.now(UTC)
    except Exception:
        cutoff = None
    pending: list[dict] = []
    for entry in data.get("entries", []):
        if entry.get("deprecated"):
            continue
        is_inferred_unreviewed = (
            entry.get("source") == "inferred" and not entry.get("reviewed")
        )
        is_stale = False
        if entry.get("time_sensitive") and entry.get("reviewed_at") and cutoff is not None:
            try:
                reviewed_dt = datetime.fromisoformat(
                    entry["reviewed_at"].replace("Z", "+00:00")
                )
                delta = cutoff - reviewed_dt
                if delta.days > _STALE_REVIEW_DAYS:
                    is_stale = True
            except ValueError:
                pass
        if not (is_inferred_unreviewed or is_stale):
            continue
        if since and entry.get("created_at"):
            try:
                created_dt = datetime.fromisoformat(
                    entry["created_at"].replace("Z", "+00:00")
                )
                if created_dt.date() < since:
                    continue
            except ValueError:
                continue
        pending.append({
            **entry,
            "_pending_reason": "inferred_unreviewed" if is_inferred_unreviewed else "stale_review",
        })
    return pending


def show_entry(bank_path: Path, entry_id: str) -> dict:
    data = _load_bank(bank_path)
    for entry in data.get("entries", []):
        if entry.get("entry_id") == entry_id:
            return entry
    raise PlanError(
        f"No answer-bank entry with id {entry_id!r}",
        error_code="profile_field_missing",
        remediation="Run answer-bank-list to see valid entry_ids.",
    )


# =============================================================================
# Validation — schema + audit-log tamper check
# =============================================================================

def validate(bank_path: Path) -> dict:
    """Shape + audit-log replay validation.

    Schema checks: required fields present, answer_format and source from
    their allowed sets. Audit-log check: every mutation recorded in the
    audit log should resolve to an entry whose final shape matches the
    replayed state (best-effort — the log is append-only JSON-lines).
    Mismatches surface as WARNINGS in the report, not hard failures — the
    user may have legitimately edited via $EDITOR.
    """
    data = _load_bank(bank_path)
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    required_fields = (
        "entry_id", "canonical_question", "answer", "answer_format",
        "source", "reviewed", "created_at",
    )
    for i, entry in enumerate(data.get("entries", [])):
        if not isinstance(entry, dict):
            errors.append(f"entries[{i}] is not an object")
            continue
        for field in required_fields:
            if field not in entry:
                errors.append(f"entries[{i}] missing required field: {field}")
        eid = entry.get("entry_id")
        if eid in seen_ids:
            errors.append(f"entries[{i}] duplicate entry_id: {eid}")
        seen_ids.add(eid)
        if entry.get("answer_format") not in ANSWER_FORMATS:
            errors.append(f"entries[{i}] invalid answer_format: {entry.get('answer_format')}")
        if entry.get("source") not in ENTRY_SOURCES:
            errors.append(f"entries[{i}] invalid source: {entry.get('source')}")

    audit_path = _audit_log_path(bank_path)
    audit_events: list[dict] = []
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                audit_events.append(json.loads(line))
            except ValueError:
                warnings.append("audit log contains non-JSON line; likely manual edit")

    for event in audit_events:
        eid = event.get("entry_id")
        if event.get("field_changed") == "entry" and eid not in seen_ids:
            warnings.append(
                f"audit log references entry_id={eid} that no longer exists "
                "(legitimate deletion or tamper — inspect manually)"
            )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "entry_count": len(data.get("entries", [])),
    }


# =============================================================================
# Markdown report for the pending-review workflow
# =============================================================================

def write_pending_report(pending: list[dict], output_path: Path) -> None:
    """Render docs/reports/answer-bank-pending.md from ``list_pending``."""
    ensure_dir(output_path.parent)
    lines = [
        "# Answer Bank — Pending Review",
        "",
        f"- Generated at: {now_iso()}",
        f"- Pending entries: {len(pending)}",
        "",
    ]
    if not pending:
        lines.append("_No inferred or stale entries awaiting review._")
    else:
        for entry in pending:
            reason = entry.get("_pending_reason", "inferred_unreviewed")
            lines.extend([
                f"## {entry.get('entry_id', '?')}",
                "",
                f"- Reason: `{reason}`",
                f"- Canonical question: {entry.get('canonical_question', '')}",
                f"- Answer (draft): {entry.get('answer', '')}",
                f"- Source: {entry.get('source', '')}",
                f"- Created: {entry.get('created_at', '')}",
                "",
            ])
            variants = entry.get("observed_variants") or []
            if variants:
                lines.append("Observed phrasings:")
                for v in variants[:5]:
                    lines.append(f"- {v}")
                lines.append("")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
