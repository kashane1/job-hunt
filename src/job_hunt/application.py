"""Application preparation and browser-adjacent orchestration.

Batch 4. Phase 1b ships only the error classes + frozen enums so downstream
modules (answer_bank, confirmation, indeed_discovery) can raise against a
stable catalog. Phase 4 populates ``prepare_application``, ``record_attempt``,
``reconcile_stale_attempts``, and ``apply_posting``. Phase 7 populates
``apply_batch``.

Module invariants (enforced by tests in Phase 1b + Phase 4):
- ``application.py`` MUST NOT import ``core.py`` — breaks the would-be
  import cycle. Shared helpers live in ``profile.py`` or ``utils.py``.
- Every raised ``ApplicationError.error_code`` and ``PlanError.error_code``
  is a member of its frozen enum. ``test_application.py`` asserts every
  raise site uses a legal code.
- ``ApplicationError`` surfaces only from runtime / browser-adjacent paths
  (per AGENTS.md:120 convention). ``PlanError`` is for pre-browser
  validation and state-machine violations.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

from .utils import StructuredError, read_json, repo_root


# =============================================================================
# Error catalog — runtime / browser-adjacent surface
# =============================================================================

APPLICATION_ERROR_CODES: Final = frozenset({
    # Session / preflight
    "session_expired",
    "session_missing",
    "already_applied",
    "posting_no_longer_available",
    # Form interaction
    "form_field_unresolved",
    "submit_button_missing",
    "resume_upload_failed",
    "cover_letter_upload_failed",
    "off_origin_form_detected",
    "prompt_injection_guard_triggered",
    # Anti-bot
    "cloudflare_challenge",
    "rate_limited_by_platform",
    "suspicious_redirect_host",
    # Budget
    "tab_budget_exhausted",
    # Confirmation
    "confirmation_email_timeout",
    "confirmation_ambiguous",
    "confirmation_sender_unverified",
    "duplicate_submission_detected",
    # Routing
    "ats_redirect_unsupported",
    "ats_redirect_out_of_scope",
    # Escalation
    "unknown_question",
    "tier_downgraded",
    # Schema / validation (raised by record_attempt before browser state reads)
    "plan_schema_invalid",
})


class ApplicationError(StructuredError):
    """Structured error raised during browser-adjacent application execution.

    Agents consume ``error_code`` (frozenset-enforced) to branch without
    string-matching. Carries ``url`` (the posting or page URL when known)
    and ``remediation`` (human-actionable guidance).
    """

    ALLOWED_ERROR_CODES = APPLICATION_ERROR_CODES


# =============================================================================
# Error catalog — pre-browser planning surface
# =============================================================================

PLAN_ERROR_CODES: Final = frozenset({
    "profile_field_missing",
    "plan_schema_invalid",
    "answer_bank_locked",
    "no_scored_leads",
    "ats_check_failed",
    "cover_letter_generation_failed",
    "resume_export_failed",
    "draft_already_exists",
    "batch_already_running",
    "account_creation_not_permitted",
    "daily_cap_reached",
    "policy_loosen_attempt",
})


class PlanError(StructuredError):
    """Structured error raised before any browser action.

    Covers profile-data gaps, schema mismatches, answer-bank contention, and
    policy violations such as attempts to override the auto-submit-tiers
    invariant. Internal helpers raise ``ValueError``; only the I/O / CLI
    boundary wraps into this structured envelope (AGENTS.md:120).
    """

    ALLOWED_ERROR_CODES = PLAN_ERROR_CODES


# Compile-time invariant anchor. The v4 policy revision requires the auto-
# submit-tiers list to be empty; any runtime-policy merge that would add a
# tier here loosens the safety posture and must be rejected. Tests enforce
# that no merge path can produce a non-empty list without a code change.
AUTO_SUBMIT_TIERS_INVARIANT: Final[tuple[str, ...]] = ()


def assert_auto_submit_invariant(policy: dict) -> None:
    """Raise ``PlanError(policy_loosen_attempt)`` if a runtime override
    attempted to enable auto-submit.

    Called from ``prepare_application``, ``apply_posting``, and
    ``apply_batch`` before any draft is produced. Per AGENTS.md Safety
    Overrides: runtime config can tighten but never loosen the default.
    """
    apply_policy = policy.get("apply_policy") if isinstance(policy, dict) else None
    if not isinstance(apply_policy, dict):
        apply_policy = {}
    tiers = apply_policy.get("auto_submit_tiers", [])
    if tiers:
        raise PlanError(
            f"auto_submit_tiers={tiers!r} is forbidden; v4 requires an empty list",
            error_code="policy_loosen_attempt",
            remediation=(
                "Remove any apply_policy.auto_submit_tiers override from runtime.yaml "
                "or --apply-policy flags. The human-submit-on-every-application invariant "
                "is compile-time enforced."
            ),
        )


# =============================================================================
# Schema introspection (Phase 1b CLIs: schemas-list, schemas-show)
# =============================================================================

_SCHEMA_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9\-]*$")
_SCHEMAS_DIR_NAME: Final = "schemas"


def _schemas_dir() -> Path:
    return repo_root() / _SCHEMAS_DIR_NAME


def list_schemas() -> list[dict]:
    """Enumerate every ``*.schema.json`` under ``schemas/``.

    Agents call this via the ``schemas-list`` CLI to discover available
    shape contracts before constructing payloads. Returned records carry
    ``name`` (without the ``.schema.json`` suffix), ``path`` (repo-relative),
    and ``version`` (the ``schema_version`` const when declared).
    """
    out: list[dict] = []
    for path in sorted(_schemas_dir().glob("*.schema.json")):
        name = path.stem.removesuffix(".schema")
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        version: int | None = None
        props = body.get("properties", {}) if isinstance(body, dict) else {}
        sv = props.get("schema_version", {}) if isinstance(props, dict) else {}
        if isinstance(sv, dict) and "const" in sv:
            version = int(sv["const"])
        out.append({
            "name": name,
            "path": f"{_SCHEMAS_DIR_NAME}/{path.name}",
            "version": version,
        })
    return out


def load_schema(name: str) -> dict:
    """Load a schema body by name. Raises ``PlanError(plan_schema_invalid)``
    when the requested schema does not exist or fails JSON parsing.
    """
    if not _SCHEMA_NAME_RE.match(name):
        raise PlanError(
            f"Invalid schema name: {name!r}",
            error_code="plan_schema_invalid",
            remediation="Schema names are lowercase kebab-case (e.g. application-plan).",
        )
    path = _schemas_dir() / f"{name}.schema.json"
    if not path.is_file():
        raise PlanError(
            f"Schema not found: {name}",
            error_code="plan_schema_invalid",
            remediation=f"Run `schemas-list` to see available schemas.",
        )
    return read_json(path)


# =============================================================================
# Preflight (Phase 1b stub → Phase 4 full implementation)
# =============================================================================

def run_preflight(policy: dict) -> dict:
    """Run the pre-application readiness checks.

    Phase 1b ships the check harness with the stable checks that already
    work (domain allowlist, answer-bank seed, apply-policy invariants,
    batch lock vacancy). Phase 4 adds the session probe and the profile
    completeness wire-up. Phase 9 adds the ToS-acknowledgment UX.

    Return shape is stable: ``{ok: bool, status: str, checks: [...]}``
    where each check has ``{name, ok, remediation?}``. CLI maps
    ``ok=False`` → exit code 2.
    """
    checks: list[dict] = []
    root = repo_root()

    allowlist_path = root / "config" / "domain-allowlist.yaml"
    checks.append({
        "name": "domain_allowlist_present",
        "ok": allowlist_path.is_file(),
        "remediation": None if allowlist_path.is_file() else (
            "Create config/domain-allowlist.yaml; see the batch 4 plan."
        ),
    })

    seed_path = root / "data" / "answer-bank.seed.json"
    checks.append({
        "name": "answer_bank_seed_present",
        "ok": seed_path.is_file(),
        "remediation": None if seed_path.is_file() else (
            "Restore data/answer-bank.seed.json from git; it ships with the repo."
        ),
    })

    working_copy = root / "data" / "answer-bank.json"
    if seed_path.is_file() and not working_copy.exists():
        try:
            working_copy.parent.mkdir(parents=True, exist_ok=True)
            working_copy.write_bytes(seed_path.read_bytes())
            copied = True
        except OSError:
            copied = False
    else:
        copied = False
    checks.append({
        "name": "answer_bank_working_copy_present",
        "ok": working_copy.is_file(),
        "remediation": None if working_copy.is_file() else (
            "Copy data/answer-bank.seed.json → data/answer-bank.json."
        ),
        "bootstrapped": copied,
    })

    try:
        assert_auto_submit_invariant(policy)
        auto_submit_ok = True
        auto_submit_reason = None
    except PlanError as exc:
        auto_submit_ok = False
        auto_submit_reason = exc.remediation or str(exc)
    checks.append({
        "name": "auto_submit_tiers_empty_invariant",
        "ok": auto_submit_ok,
        "remediation": auto_submit_reason,
    })

    batch_lock = root / "data" / "applications" / "batches" / ".lock"
    checks.append({
        "name": "no_stale_batch_lock",
        "ok": not batch_lock.exists(),
        "remediation": None if not batch_lock.exists() else (
            "Inspect data/applications/batches/.lock — if no apply-batch is running, "
            "delete it manually. (Phase 7 adds heartbeat-based stale detection.)"
        ),
    })

    # Phase 4 will add session probe + profile completeness wire-in. Mark
    # these as deferred here so the overall status is honest about scope.
    checks.append({
        "name": "chrome_session_probe",
        "ok": False,
        "remediation": "Session probe lands in Phase 4; run phase-1b preflight for scaffolding only.",
        "deferred_to_phase": 4,
    })

    overall_ok = all(c["ok"] for c in checks if not c.get("deferred_to_phase"))
    return {
        "ok": overall_ok,
        "status": "ok" if overall_ok else "incomplete",
        "checks": checks,
    }
