"""Validation helper for the profile / resume-lane source of truth.

`profile-doctor` checks that the resume variant registry, the per-lane resume
files, the authoring templates, and the claims truth bank are mutually
consistent — and that no real private artifact has been accidentally committed.
Read-only: it reports, it never writes.

Findings carry a level: ``error`` (must fix), ``warn`` (should fix), ``info``.
The CLI exits non-zero when any ``error`` is present.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

from .resume_registry import RegistryError, load_registry
from .utils import read_json, repo_root

DEFAULT_CLAIMS_REL: Final = "profile/claims/claims-bank.json"
EXAMPLE_CLAIMS_REL: Final = "profile/claims/claims-bank.example.json"
TEMPLATES_DIR_REL: Final = "profile/resumes/templates"

# Statuses describing a resume authored locally but intentionally gitignored.
# A clean checkout will not have the file, so its absence is never an error.
PRIVATE_STATUSES: Final = frozenset({"draft_private", "needs_user_review", "ready_local"})


def _finding(level: str, code: str, message: str) -> dict:
    return {"level": level, "code": code, "message": message}


# --- claims bank -------------------------------------------------------------

def load_claims_bank(path: Path) -> dict | None:
    """Load a claims bank, or None if absent. Never raises on a missing file."""
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def approved_claims_by_lane(claims_bank: dict | None) -> dict[str, int]:
    """Count claims with review_status==approved, keyed by allowed lane id."""
    counts: dict[str, int] = {}
    if not claims_bank:
        return counts
    for claim in claims_bank.get("claims", []) or []:
        if claim.get("review_status") != "approved":
            continue
        for lane in claim.get("allowed_lanes", []) or []:
            counts[lane] = counts.get(lane, 0) + 1
    return counts


# --- path privacy classification --------------------------------------------

_PRIVATE_DIR_PREFIXES: Final = (
    "profile/raw/",
    "profile/private-review/",
    "profile/normalized/",
    "data/generated/",
    "data/applications/",
    "data/leads/",
    "data/runs/",
    "data/discovery/",
    "data/calibration/",
    "data/companies/",
)


def is_private_path(rel_path: str) -> bool:
    """True if a repo-relative path holds real PII/generated data that must not
    be tracked. The tracked scaffolding (READMEs, templates, sanitized examples)
    is explicitly excluded."""
    p = rel_path.replace("\\", "/")
    if any(p.startswith(prefix) for prefix in _PRIVATE_DIR_PREFIXES):
        return True
    # Real resume lane files: profile/resumes/*.md, except README + templates/.
    if p.startswith("profile/resumes/") and p.endswith(".md"):
        if p == "profile/resumes/README.md":
            return False
        if p.startswith("profile/resumes/templates/"):
            return False
        return True
    # Real claims bank: profile/claims/*.json, except the sanitized example.
    if p.startswith("profile/claims/") and p.endswith(".json"):
        return p != EXAMPLE_CLAIMS_REL
    # Generated profile reports + internal audit reports under docs/reports/.
    # `profile-*.md` files (profile-document-audit.md, profile-completeness.md, …)
    # are normalized-profile output and carry PII/private source references. The
    # tracked README scaffolding stays public.
    if p.startswith("docs/reports/"):
        name = p[len("docs/reports/"):]
        if name == "README.md":
            return False
        if name.startswith("profile-") and name.endswith(".md"):
            return True
        if p.endswith("-report.md") or "-audit-" in p:
            return True
    return False


def check_no_private_tracked(root: Path) -> list[dict]:
    """Flag any git-tracked file that is_private_path. Degrades to an info note
    if git is unavailable (not a repo, git missing)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return [_finding("info", "git_unavailable",
                         "could not enumerate tracked files (git not available)")]
    tracked = [p for p in out.split("\0") if p]
    findings = [
        _finding("error", "private_tracked",
                 f"private/PII path is tracked by git: {p}")
        for p in tracked if is_private_path(p)
    ]
    return findings


# --- lane consistency --------------------------------------------------------

def template_filename(variant_id: str) -> str:
    return f"{variant_id.replace('_', '-')}.template.md"


def check_lanes(registry: dict, claims_bank: dict | None, root: Path) -> dict:
    """Check registry lanes against resume files, templates, and approved claims."""
    findings: list[dict] = []
    approved = approved_claims_by_lane(claims_bank)
    default_id = registry["default_variant"]
    lanes: list[dict] = []

    for variant in registry["variants"]:
        vid = variant["id"]
        resume_path = variant.get("resume_path", "")
        review_status = variant.get("review_status", "draft")
        resume_exists = bool(resume_path) and (root / resume_path).exists()
        approved_count = approved.get(vid, 0)
        template_exists = (root / TEMPLATES_DIR_REL / template_filename(vid)).exists()
        is_default = vid == default_id

        if not resume_path:
            findings.append(_finding("error", "no_resume_path",
                                     f"lane '{vid}' has no resume_path"))

        is_private = review_status in PRIVATE_STATUSES
        if review_status == "ready":
            if not resume_exists:
                findings.append(_finding(
                    "error", "ready_but_missing_resume",
                    f"lane '{vid}' is marked ready but resume file is missing: {resume_path}"))
            if approved_count == 0:
                findings.append(_finding(
                    "error", "ready_but_no_claims",
                    f"lane '{vid}' is marked ready but has no approved claims"))
        elif is_private:
            # Resume is authored locally but gitignored; absence on a clean
            # checkout is by design, so this is informational, never blocking.
            findings.append(_finding(
                "info", "private_lane",
                f"lane '{vid}' is {review_status}; resume is local-only "
                f"(present here: {resume_exists}, approved_claims={approved_count})"))
        elif not resume_exists:
            # Expected during authoring; surface so it is visible, not blocking.
            findings.append(_finding(
                "warn", "resume_source_missing",
                f"lane '{vid}' resume file not authored yet: {resume_path or '(none)'}"))

        if not template_exists:
            findings.append(_finding(
                "warn", "missing_template",
                f"lane '{vid}' has no authoring template "
                f"({TEMPLATES_DIR_REL}/{template_filename(vid)})"))

        lanes.append({
            "id": vid,
            "is_default": is_default,
            "resume_path": resume_path,
            "resume_exists": resume_exists,
            "review_status": review_status,
            "approved_claims": approved_count,
            "template_exists": template_exists,
            "ready": review_status in ("ready", "ready_local") and resume_exists and approved_count > 0,
        })

    # Fallback/default behavior must be explicit and resolvable.
    default_lane = next((l for l in lanes if l["is_default"]), None)
    if default_lane is None:
        findings.append(_finding("error", "no_default_lane",
                                 "registry default_variant does not resolve to a lane"))
    elif not default_lane["resume_exists"]:
        findings.append(_finding(
            "warn", "default_unresolved",
            f"default lane '{default_lane['id']}' resume file does not exist; "
            f"fallback routing cannot produce a real resume"))

    if claims_bank is None:
        findings.append(_finding(
            "info", "claims_bank_absent",
            f"no claims bank at {DEFAULT_CLAIMS_REL} (copy from the example to enable lane readiness)"))

    return {"lanes": lanes, "findings": findings}


def run_doctor(
    *,
    registry_path: Path | None = None,
    claims_path: Path | None = None,
    root: Path | None = None,
) -> dict:
    """Full profile-doctor report."""
    root = root or repo_root()
    claims_path = claims_path or (root / DEFAULT_CLAIMS_REL)
    registry = load_registry(registry_path)
    claims_bank = load_claims_bank(claims_path)

    report = check_lanes(registry, claims_bank, root)
    report["findings"].extend(check_no_private_tracked(root))

    findings = report["findings"]
    report["counts"] = {
        "error": sum(1 for f in findings if f["level"] == "error"),
        "warn": sum(1 for f in findings if f["level"] == "warn"),
        "info": sum(1 for f in findings if f["level"] == "info"),
    }
    report["ok"] = report["counts"]["error"] == 0
    return report


def format_report(report: dict) -> str:
    lines = ["profile-doctor", ""]
    for lane in report["lanes"]:
        tag = " (default)" if lane["is_default"] else ""
        ready = "ready" if lane["ready"] else lane["review_status"]
        lines.append(
            f"  - {lane['id']}{tag}: resume={'yes' if lane['resume_exists'] else 'MISSING'}, "
            f"template={'yes' if lane['template_exists'] else 'no'}, "
            f"approved_claims={lane['approved_claims']}, status={ready}"
        )
    lines.append("")
    icon = {"error": "✗", "warn": "!", "info": "·"}
    for f in report["findings"]:
        lines.append(f"  {icon.get(f['level'], '?')} [{f['level']}] {f['code']}: {f['message']}")
    c = report["counts"]
    lines.append("")
    lines.append(f"  {c['error']} error(s), {c['warn']} warning(s), {c['info']} info")
    return "\n".join(lines)
