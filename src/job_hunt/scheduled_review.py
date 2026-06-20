"""Scheduled-run orchestration for the safe, browserless new-jobs review.

This composes the pieces that already exist — public discovery, the watcher
readiness queue, capped packet preparation, automatic PDF export (performed
inside ``prepare_application``), and the read-only packet review — into one
repeatable command suitable for a cron / launchd schedule.

It NEVER applies, opens a browser, fills a form, creates an account, or submits.
Generated packets remain human-submit only; the scheduled run simply prepares at
most a hard-capped number of them and reports what a human should look at next.

This module holds the pure, side-effect-free pieces (candidate selection,
guardrail evaluation, report assembly) so they are cheap to unit-test with
synthetic fixtures. The CLI handler in ``core.py`` wires the heavyweight
callables (``discover_jobs``, ``prepare_application``, ``review_packets``) around
them.
"""

from __future__ import annotations

from typing import Final

# Guardrail verdicts. ``fail`` should gate a scheduled run (non-zero exit);
# ``warn`` surfaces a problem without blocking; ``ok`` is clean.
GUARDRAIL_OK: Final = "ok"
GUARDRAIL_WARN: Final = "warn"
GUARDRAIL_FAIL: Final = "fail"
GUARDRAIL_STATUSES: Final = (GUARDRAIL_OK, GUARDRAIL_WARN, GUARDRAIL_FAIL)

# A conservative default packet cap. The CLI enforces this as a hard ceiling: a
# negative value is clamped to 0 (never generates), and selection never exceeds
# the resolved cap.
DEFAULT_MAX_PACKETS: Final = 2

# Private paths a scheduled run must never leak into git. Surfaced to the
# gitignore guardrail; the CLI resolves the concrete file list (e.g. per-lane
# resumes) before checking.
PRIVATE_PATHS: Final = (
    "config/watchlist.yaml",
    "data/generated",
    "data/applications",
    "data/leads",
    "profile/claims/claims-bank.json",
)


def resolve_max_packets(value: object, *, default: int = DEFAULT_MAX_PACKETS) -> int:
    """Coerce a requested packet cap to a non-negative int.

    A missing/None value falls back to ``default``; negatives clamp to 0 so a
    scheduled run can be told to generate nothing (``--max-packets 0``) for a
    pure read-only review.
    """
    if value is None:
        return max(0, int(default))
    try:
        n = int(value)
    except (TypeError, ValueError):
        return max(0, int(default))
    return max(0, n)


def select_packet_candidates(queue: dict, *, max_packets: int) -> list[str]:
    """Lead IDs of the top ``packet_ready`` items to prepare this run.

    The queue is already ranked (packet_ready first, then by score), so this just
    takes the leading ``packet_ready`` items up to the cap. Items already flagged
    ``hidden`` (seen in a prior run with --hide-seen) are skipped so a schedule
    does not re-surface the same lead every tick. Returns ``[]`` when the cap is
    zero or there are no ready leads.
    """
    if max_packets <= 0:
        return []
    out: list[str] = []
    for it in queue.get("items", []):
        if it.get("status") != "packet_ready":
            continue
        if it.get("hidden"):
            continue
        lead_id = it.get("lead_id")
        if not lead_id:
            continue
        out.append(lead_id)
        if len(out) >= max_packets:
            break
    return out


def generated_pdf_summary(generated_reviews: list[dict]) -> dict:
    """Aggregate PDF-export outcomes across packets generated *this run*.

    ``generated_reviews`` are packet-review records (from ``review_packets``)
    filtered to the draft IDs prepared this run. Reads only the ``pdf.overall``
    metadata field — never any prose.
    """
    ready = failed = pending = 0
    for r in generated_reviews:
        overall = (r.get("pdf") or {}).get("overall")
        if overall == "ready":
            ready += 1
        elif overall == "failed":
            failed += 1
        else:
            pending += 1
    return {
        "prepared": len(generated_reviews),
        "pdf_ready": ready,
        "pdf_failed": failed,
        "pdf_pending": pending,
    }


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #
def _verdict(check: str, status: str, detail: str) -> dict:
    return {"check": check, "status": status, "detail": detail}


def evaluate_doctor_guardrail(*, errors: int, warnings: int, strict: bool = False) -> dict:
    """profile-doctor must be clean. Errors always fail; warnings warn (or fail
    under ``strict``)."""
    if errors > 0:
        return _verdict("profile_doctor", GUARDRAIL_FAIL,
                        f"{errors} error(s), {warnings} warning(s)")
    if warnings > 0:
        return _verdict("profile_doctor",
                        GUARDRAIL_FAIL if strict else GUARDRAIL_WARN,
                        f"{warnings} warning(s)")
    return _verdict("profile_doctor", GUARDRAIL_OK, "clean")


def evaluate_gitignore_guardrail(ignore_map: dict[str, bool], *, strict: bool = True) -> dict:
    """Every private path must be gitignored. Any leak fails (default strict).

    ``ignore_map`` maps a private path to whether git ignores it. Path *names*
    are non-private; their contents are never read here.
    """
    if not ignore_map:
        return _verdict("gitignore", GUARDRAIL_WARN, "no private paths checked")
    leaks = sorted(p for p, ignored in ignore_map.items() if not ignored)
    if leaks:
        return _verdict("gitignore",
                        GUARDRAIL_FAIL if strict else GUARDRAIL_WARN,
                        f"{len(leaks)} private path(s) NOT ignored: " + ", ".join(leaks))
    return _verdict("gitignore", GUARDRAIL_OK,
                    f"all {len(ignore_map)} private path(s) ignored")


def evaluate_pdf_guardrail(pdf_summary: dict, *, strict: bool = False) -> dict:
    """PDF export of generated packets should succeed. Failures warn (or fail
    under ``strict``). No generated packets is OK (nothing to export)."""
    prepared = pdf_summary.get("prepared", 0)
    failed = pdf_summary.get("pdf_failed", 0)
    if prepared == 0:
        return _verdict("pdf_export", GUARDRAIL_OK, "no packets generated this run")
    if failed > 0:
        return _verdict("pdf_export",
                        GUARDRAIL_FAIL if strict else GUARDRAIL_WARN,
                        f"{failed}/{prepared} generated packet(s) failed PDF export")
    return _verdict("pdf_export", GUARDRAIL_OK,
                    f"{pdf_summary.get('pdf_ready', 0)}/{prepared} PDF(s) ready")


def overall_guardrail_status(guardrails: list[dict]) -> str:
    """Worst status across guardrails (fail > warn > ok)."""
    statuses = {g.get("status") for g in guardrails}
    if GUARDRAIL_FAIL in statuses:
        return GUARDRAIL_FAIL
    if GUARDRAIL_WARN in statuses:
        return GUARDRAIL_WARN
    return GUARDRAIL_OK


# --------------------------------------------------------------------------- #
# Discovery summary (non-private)
# --------------------------------------------------------------------------- #
def discovery_brief(discovery: dict | None) -> dict:
    """A compact non-private summary of a discovery pass for the report.

    Carries source/company identifiers and bucket counts only — the same
    public metadata discovery already persists. ``None`` (offline run) yields a
    disabled brief.
    """
    if not discovery:
        return {"ran": False, "mode": "offline"}
    if discovery.get("status") == "skipped_gap":
        return {
            "ran": False,
            "mode": "skipped_gap",
            "error_code": discovery.get("error_code"),
            "detail": discovery.get("message") or discovery.get("error"),
        }
    counts = discovery.get("counts") or {}
    sources_run = discovery.get("sources_run") or []
    sources = sorted({s.get("source") for s in sources_run if s.get("source")})
    return {
        "ran": True,
        "mode": "discovery",
        "sources_contacted": sources,
        "companies_contacted": len(sources_run),
        "newly_ingested": counts.get("discovered", 0),
        "already_known": counts.get("already_known", 0),
        "duplicate_within_run": counts.get("duplicate_within_run", 0),
        "skipped_by_budget": counts.get("skipped_by_budget", 0),
        "dropped_by_url_guard": counts.get("dropped_by_url_guard", 0),
        "failed": counts.get("failed", 0),
    }


# --------------------------------------------------------------------------- #
# Next human action
# --------------------------------------------------------------------------- #
def next_human_action(
    *,
    guardrail_status: str,
    generated_count: int,
    queue_counts: dict,
    review_summary: dict,
    max_packets: int,
    packets_review_cmd: str,
) -> dict:
    """The single most useful, safe next step for the human.

    Never an auto-submit. The most decisive condition wins, in order: a failing
    guardrail, freshly generated packets to review, ready leads that the cap held
    back, leads needing review, otherwise widen/discover.
    """
    if guardrail_status == GUARDRAIL_FAIL:
        return {
            "kind": "resolve_guardrail",
            "message": "A guardrail failed; resolve it before relying on this run.",
            "command": None,
        }
    if generated_count > 0:
        return {
            "kind": "review_generated_packets",
            "message": f"{generated_count} packet(s) prepared (human-submit only). "
                       "Review them, then submit by hand.",
            "command": packets_review_cmd,
        }
    ready = queue_counts.get("packet_ready", 0)
    if ready > 0 and max_packets == 0:
        return {
            "kind": "raise_cap",
            "message": f"{ready} packet-ready lead(s) found but the packet cap is 0. "
                       "Re-run with --max-packets >= 1 to prepare them.",
            "command": None,
        }
    if review_summary.get("needs_attention", 0) > 0:
        return {
            "kind": "resolve_attention",
            "message": f"{review_summary['needs_attention']} existing packet(s) need attention.",
            "command": packets_review_cmd + " --needs-attention",
        }
    if queue_counts.get("needs_review", 0) > 0:
        return {
            "kind": "review_leads",
            "message": f"{queue_counts['needs_review']} lead(s) need review before a packet.",
            "command": None,
        }
    return {
        "kind": "widen_or_discover",
        "message": "Nothing actionable this run; widen the lookback window or run discovery.",
        "command": None,
    }


def _summary_row(item: dict) -> dict:
    """A safe top-row (public posting metadata + lane/score only)."""
    return {
        "company": item.get("company", ""),
        "title": (item.get("title") or "").strip(),
        "lane": item.get("selected_lane") or item.get("lane"),
        "score": item.get("score"),
        "status": item.get("status"),
    }


def build_report(
    *,
    since_hours: float,
    max_packets: int,
    dry_run: bool,
    generated_at: str,
    discovery: dict | None,
    queue: dict,
    generated: list[dict],
    generated_pdf: dict,
    review_summary: dict,
    guardrails: list[dict],
    top_rows: list[dict],
    next_action: dict,
) -> dict:
    """Assemble the safe aggregate scheduled-run report.

    Carries only non-private content: counts, booleans, public posting metadata,
    lane IDs, and reason codes. No resume/cover-letter prose, no claim text, no
    raw preference values.
    """
    counts = queue.get("totals", {}) or {}
    brief = discovery_brief(discovery)
    return {
        "schema_version": 1,
        "kind": "scheduled_review",
        "generated_at": generated_at,
        "window": {
            "since_hours": since_hours,
            "leads_considered": queue.get("leads_considered", len(queue.get("items", []))),
            "source_mode": "discovery" if brief.get("ran") else "offline",
        },
        "discovery": brief,
        "queue": {
            "packet_ready": counts.get("packet_ready", 0),
            "needs_review": counts.get("needs_review", 0),
            "reject": counts.get("reject", 0),
            "dropped_stale": queue.get("dropped_stale", 0),
            "dropped_for_cap": queue.get("dropped_for_cap", 0),
            "already_packeted": queue.get("already_packeted", 0),
        },
        "packets_generated": {
            "cap": max_packets,
            "dry_run": dry_run,
            "count": len(generated),
            "drafts": generated,
            "pdf": generated_pdf,
        },
        "packet_queue": {
            "total": review_summary.get("total", 0),
            "ready_for_review": review_summary.get("ready_for_review", 0),
            "needs_attention": review_summary.get("needs_attention", 0),
            "safety_errors": review_summary.get("safety_errors", 0),
        },
        "top_packets": [_summary_row(r) for r in top_rows],
        "guardrails": guardrails,
        "guardrail_status": overall_guardrail_status(guardrails),
        "next_action": next_action,
        "safety": [
            "No apply, browser, form, account, or submit actions were taken.",
            "Generated packets are human-submit only and must be reviewed by hand.",
            "Private preferences are summarized as counts/booleans only.",
            f"Packet generation is hard-capped at {max_packets} this run.",
        ],
    }
