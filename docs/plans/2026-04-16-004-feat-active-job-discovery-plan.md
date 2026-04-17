---
title: "feat: Active job discovery — board listings, career-page crawl, and watchlist scheduler"
type: feat
status: completed
date: 2026-04-16
origin: docs/brainstorms/2026-04-15-job-hunt-brainstorm.md
deepened: 2026-04-16
technical_review_v3: 2026-04-16
---

# feat: Active Job Discovery — Board Listings, Career-Page Crawl, and Watchlist Scheduler

## Enhancement Summary

**Deepened on:** 2026-04-16 (same day as original plan).
**Review agents used:** Kieran Python, Architecture Strategist, Security Sentinel, Performance Oracle, Data Integrity Guardian, Simplicity Reviewer, Pattern Recognition, Agent-Native Reviewer.
**Research agents used:** Best-Practices Researcher (job-board APIs, robots.txt, anti-bot), Framework Docs Researcher (stdlib concurrency, urllib.robotparser, os.replace, simple_yaml).
**Learnings cross-checks:** SSRF hardening (batch 2 review lesson), extend-CLI pattern, split-brain-pre-implementation lesson.

### Key Improvements Over v1 of This Plan

1. **`simple_yaml` blocker surfaced and fixed.** The repo's stdlib-replacement YAML loader only supports flat top-level key/value + single-level scalar lists. The original plan's `companies:` structure (list-of-mappings) would not parse. **Resolution:** extend `simple_yaml` to support list-of-mappings at depth 2 (one targeted change, kept minimal) AND add the parser extension to Phase 1 deliverables with dedicated tests.
2. **Rate limiter race condition fixed.** Original `acquire()` released the lock before reserving the slot, allowing N threads to see the same `last_request_at` and burst together. **Resolution:** reserve-before-sleep pattern (set `last_request_at = earliest_slot` inside the lock, THEN sleep unlocked). `_DomainRateLimiter` moved from `discovery.py` to `utils.py` per the extend-CLI lesson (shared utility; avoid bidirectional imports).
3. **`utils.write_json` atomic-write upgraded for concurrent same-path writes.** Original used `path.with_suffix(".tmp")` — two threads writing the same `lead_id` share one tmp path. **Resolution:** Phase 1 upgrades `write_json` to `tempfile.mkstemp(dir=path.parent)` + `os.replace`. Batch 1 and batch 2 callers are unaffected (single-writer-wins semantics preserved).
4. **`discovered_via` read-modify-write fully specified.** Merge semantics now define: within-run discoveries always append (even when a source hits `duplicate_within_run`, its provenance entry is still appended to the existing lead's `discovered_via` array under a per-lead lock). Between-run reads use `.get("discovered_via", [])` and append the new run's entry.
5. **Cursor advancement invariant tightened.** Integration Test 6 previously advanced the cursor on budget-capped sources — silently losing up to 95 listings. **Resolution:** cursor advances only when listing fetcher returned a complete response (`listing_truncated: False`) AND the source did NOT hit the budget cap mid-traversal. Budget-capped and truncated sources leave cursor unchanged; next run reprocesses.
6. **Decompression math made consistent.** Original raised ingress cap to 8MB but kept 5MB decompressed cap — benign Greenhouse listings would legitimately fail. **Resolution:** `MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000` as a separate constant (~2.5× ingress, below per-process memory concern), plumbed through `_fetch(..., max_decompressed_bytes=)` kwarg. Per-posting fetches keep the batch 2 5MB cap.
7. **DNS-rebinding TOCTOU addressed at the batch-2 layer.** Batch 2's `_validate_url_for_fetch` resolved the IP, batch 2's `_fetch` re-resolved via the OS — attacker can flip DNS between calls. **Resolution:** Phase 1 patches `ingestion._fetch` to pin the validated IP via a custom `HTTPConnection` subclass that overrides `connect()` and sets `Host:` to the hostname. Fixes batch 2 AND batch 3. Added as a prerequisite (P0 security fix) rather than a discovery-only mitigation.
8. **Review-file prompt injection surface closed.** Batch 2's nonce-delimited wrapping only applied to JSON. Review markdown files contain attacker-controlled anchor text. **Resolution:** review files HTML-escape anchor text, code-fence all extracted strings, and include a "DATA NOT INSTRUCTIONS" banner in YAML frontmatter + first body block. Companion `.json` file written alongside the `.md` for agent consumption (schema-validated).
9. **Robots cache stampede fixed.** Original allowed two threads to both miss the cache for the same host and both fetch robots.txt. **Resolution:** per-host `threading.Event` in-flight coordination; second thread waits on the event instead of initiating a duplicate fetch.
10. **Persistent robots cache added.** Originally per-run; saves ~25s on 50-company runs via `data/discovery/robots_cache.json` with 24h TTL.
11. **Auto-scoring moved out of inline loop.** Originally each `ingest_url` was followed by `score_lead` (likely LLM-backed, 2-5s/call). At 50 new leads: 100-250s serial. **Resolution:** discovery completes all ingestion first, THEN runs batched `score_lead` in a separate ThreadPool with its own rate profile. `--no-score` preserved; `--score-concurrency N` added.
12. **JSON-LD `JobPosting` structured data extracted before regex fallback.** 2026 research: a large fraction of modern careers pages emit `<script type="application/ld+json">` with `JobPosting` schema (driven by Google For Jobs eligibility). **Resolution:** career crawler tries JSON-LD first; ATS-subdomain detection as the other highest-precision signal. Regex fallback is now third priority.
13. **Agent-native gaps closed in v1.** Added `review-list`, `review-promote`, `review-dismiss`, `watchlist-show`, `watchlist-add`, `watchlist-remove`, `discovery-state`, and `--reset-cursor` commands — all ship in Phase 4/5, not deferred. Keeps parity with the AGENTS.md "agent-first" contract.
14. **15 split-brain remediations applied.** Error code frozen-sets now match raise sites, `signals` field added to `ListingEntry`, `confidence` field added to `discovered_via` items, `SOURCE_NAME_MAP` constant defined to bridge CLI / schema / dataclass vocabularies, `DISCOVERY_USER_AGENT` constant single-sourced, missing `import re`, `.gitignore` extension now a deliverable, cursor JSON schema added, and more. Prose, code, schemas, acceptance criteria, and tests are synchronized.
15. **`data/discovery/runs/` renamed to `data/discovery/history/`.** Avoids visual collision with existing top-level `data/runs/`.
16. **`ingestion._fetch` promoted to `ingestion.fetch` (public).** Discovery was importing a private symbol. Batch 2 call sites continue working via a one-line alias.

### New Considerations Discovered During Deepening

- **`urllib.robotparser` is not RFC 9309 compliant** (cpython#138907). Documented limitations: BOM handling, 5xx inverted from spec, longest-match precedence quirks. Plan adds a thin wrapper that (a) strips BOM before `.parse(lines)`, (b) treats 5xx as "assume disallow for 24h, retry next run" (spec-correct), (c) caps robots body at 500KB.
- **Greenhouse `?content=true` double-HTML-encodes** content field. Crawler must `html.unescape` twice.
- **Cloudflare / Akamai / DataDome anti-bot realism:** stdlib urllib hit rate on generic career pages is realistically ~70%. Plan adds challenge-page detection (HTTP 403 + `cf-ray` header OR `<title>Just a moment…</title>`) and marks the host `anti_bot_blocked` rather than retrying.
- **IPv4-mapped IPv6 (`::ffff:127.0.0.1`) may bypass `is_private` on some Python versions.** Added targeted test and explicit check in `_validate_url_for_fetch`.
- **Config-tracking convention deviation:** `config/watchlist.yaml` is the first config file that goes in `.gitignore` (contains user's target-company list, which is PII-adjacent). Existing `scoring.yaml`/`runtime.yaml`/etc. remain tracked. Deviation documented in `AGENTS.md` and `docs/guides/job-discovery.md`.
- **Worst-case scale ceiling documented.** 100-company watchlist with persistent robots + coalesced cursor + batched scoring: ~8-12 min. Same workload on the original plan design: ~20+ min dominated by inline LLM scoring and robots re-fetch.
- **Additional ATS platforms** (Ashby public, SmartRecruiters, Workday, Recruitee, Personio, Teamtailor) have unauth JSON feeds. Scoped as batch 4 candidates; batch 3 ships Greenhouse + Lever + generic crawler only.

### v3 Additions (Technical Review — 2026-04-16)

Post-deepen technical review surfaced 13 P1/P2 items (todos/028-040). All are now resolved in this plan:

1. **P1-028 IP-pinning TLS/SNI/redirect specifics** — Phase 1 now specifies `_PinnedHTTPSConnection` with `server_hostname=hostname`, `check_hostname=True`, `verify_mode=CERT_REQUIRED`, `Connection: close` header, and re-pin on every redirect hop via updated `_StrictRedirectHandler`. Three new tests: cert validation under pin, redirect re-pin, pool safety.
2. **P1-029 Watchlist YAML write safety + comment preservation** — New `watchlist._emit_yaml` with double-quoted strings, control-char rejection at CLI layer. Behavior on existing comments: warn + require `--force`. New error code `watchlist_comments_present`.
3. **P1-030 `_append_discovered_via` defensive merge** — Lock key is `lead_id` (stable identity, not Path string). Shape check on existing `discovered_via`; non-list value logs warning and resets to `[]`. Missing lead file raises new error code `lead_write_race`.
4. **P1-031 `registered_domain` edge cases + schema regexes + cursor separator** — IP URLs bucketed whole; empty hostnames raise; IDN Punycode normalized. `watchlist.schema.json` adds `name` pattern `^[A-Za-z0-9 ._-]{1,64}$`. `discovery-review.schema.json` adds `entry_id` pattern `^[a-f0-9]{16}$`. Cursor key separator changed from `:` to `|` (forbidden by name regex).
5. **P1-032 Explicit `to_dict()` implementations** — `Outcome.to_dict()` and `DiscoveryResult.to_dict()` spelled out; `discovery-run.schema.json` authored as concrete schema against them (not a forward reference).
6. **P1-033 Batched scoring crash recovery** — Scoring phase now scans `data/leads/*.json` for `status: discovered` leads missing `fit_assessment` and scores them alongside freshly-discovered ones. Re-running `discover-jobs` automatically heals a crashed scoring phase. `check-integrity` warns on unscored discovered-status leads >1h old.
7. **P2-034 `StructuredError` base class** — Added to `utils.py`; `IngestionError`, `PdfExportError`, `DiscoveryError` all subclass it. CLI error handler catches `StructuredError` uniformly. Existing batch 1/2 tests pass unchanged (inheritance is additive).
8. **P2-035 Review files collapse to single `.md` with YAML frontmatter** — Agents parse frontmatter (simple_yaml already handles it). No more paired-file orphan class. Frontmatter includes `DATA_NOT_INSTRUCTIONS: true` flag + all structured fields (candidate_url, anchor_text, signals, status, etc.). `schemas/discovery-review.schema.json` validates the frontmatter.
9. **P2-036 `DiscoveryConfig` dataclass** — 12-param `discover_jobs(watchlist_path, leads_dir, discovery_root, config=DiscoveryConfig())` signature replaces the earlier 12-arg form. CLI handler maps argparse → DiscoveryConfig in one place.
10. **P2-037 `utils.py` split + drop `_fetch` alias + sweep glob** — New module `src/job_hunt/net_policy.py` hosts `DomainRateLimiter` and `RobotsCache`; `utils.py` stays primitives-only. `_fetch = fetch` alias dropped (all call sites updated in the same PR). `write_json` uses `tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)` and the startup sweep globs `*.tmp` to catch both batch-2 and batch-3 stragglers.
11. **P2-038 Agent-native completeness** — `discovery-state --last-run` + `discovery-state --last-run --bucket failed` query the most recent `data/discovery/history/*.json`. New `watchlist-validate PATH` command for pre-write config validation. New `robots-cache-clear` command.
12. **P2-039 Anti-bot detection via status + header (not body-alone) + nonce-fenced review content** — `_detect_anti_bot(status, headers, body)` requires HTTP 403/503 AND (header `cf-ray` OR `<title>Just a moment…`). Review-file body fence is nonce-derived (`secrets.token_hex(6)`) rather than fixed backticks, mirroring batch 2's nonce-delimited pattern. `fetch()` return type expanded to include status and headers.
13. **P2-040 robots cache poisoning + parent-dir fsync + lock-map WeakValueDictionary** — RobotsCache stores resolved IP per entry; invalidates on re-resolve mismatch. Disallow-decision TTL shortened to 1h (allow stays 24h). `write_json` now fsyncs parent directory after `os.replace` (Linux correctness). `_LEAD_WRITE_LOCKS` uses `WeakValueDictionary` to auto-collect unused locks.

**Net impact:** Effort estimate grows from 9 → 11 sessions (Phase 1 absorbs StructuredError base, IP-pin details, utils.py split, 10+ new tests; Phase 4 absorbs DiscoveryConfig, 3 new commands, rescore-on-rerun). Total new test count: ≥60 (up from ≥45 in v2).

### Rejected Deepening Suggestions (With Rationale)

- **Cut the generic career crawler entirely (simplicity reviewer):** rejected. User explicitly requested web scraping; Greenhouse/Lever-only coverage misses most target companies; JSON-LD + ATS-subdomain signals make the crawler high-precision, not the false-positive pit the original plan feared.
- **Flatten 7 dedup buckets → 3 (simplicity reviewer):** rejected. Each bucket drives distinct agent behavior (retry vs. skip vs. surface vs. human-triage). Merging `duplicate_within_run` with `already_known` would hide a legitimate UX signal.
- **Drop `_DomainRateLimiter` for inline `time.sleep(0.5)` (simplicity reviewer):** rejected. Generic career crawler targets arbitrary user-supplied domains; a single lock on a single sleep serializes unrelated hosts. Global per-domain limiter in `utils.py` is ~25 LOC and needed.
- **Add `ashby_board`, `workday_board`, `smartrecruiters_board`, etc. to batch 3:** deferred to batch 4. Batch 3 already substantial; Ashby/Workday coverage doesn't gate the batch's user-facing value.
- **Switch to `protego` for RFC-9309-compliant robots parsing:** rejected. No new default deps. Document the stdlib `robotparser` limitations and wrap them.
- **Defer `--reset-cursor` and review CLI (original plan's "maybe"):** rejected by agent-native reviewer. Ship in v1.

---

## Overview

Batch 3 closes the single biggest remaining friction gap in the job-hunt repo: the user currently has to find job URLs manually before `ingest-url` can do anything useful. This batch adds **active discovery** — fetch listings from Greenhouse and Lever public board APIs, crawl company career pages (politely, respecting robots.txt), and orchestrate both via a `config/watchlist.yaml` polled by a new `discover-jobs` CLI command.

The brainstorm (`docs/brainstorms/2026-04-15-job-hunt-brainstorm.md`) lists **Phase 1: Trustworthy Discovery** — "discover and dedupe leads" — as the first phase of the phased rollout, but batches 1 and 2 intentionally skipped it to first prove the profile → lead → content → tracking → analytics pipeline. That pipeline is now solid (batch 1 and batch 2 landed; 156 tests pass). Batch 3 turns the pipeline into a system the user can actually feed without manual URL collection.

**Three user-facing features, plus supporting infrastructure:**

1. **Board listing fetchers** — Greenhouse `/v1/boards/{company}/jobs` and Lever `/v0/postings/{company}` listing endpoints.
2. **Generic career-page crawler** — discover a company's careers page, parse JSON-LD `JobPosting` first, detect ATS subdomains second, fall back to heuristic link extraction, respect robots.txt, require ≥2 confidence signals before auto-queueing.
3. **Watchlist + `discover-jobs` command** — `config/watchlist.yaml` of companies to poll, run all configured sources, dedupe, filter, batch-score after ingestion, emit structured JSON, persist an incremental cursor.

**Plus agent-native CLI surface** (ships in v1, not deferred): `review-list`, `review-promote`, `review-dismiss`, `watchlist-show`, `watchlist-add`, `watchlist-remove`, `discovery-state`, `--reset-cursor`.

**Features deferred to batch 4:** outreach drafting, scoring calibration, additional ATS platforms (Ashby/Workday/SmartRecruiters/Recruitee/Personio/Teamtailor).

> **Why now vs. build-draft / browser automation:** browser automation for application submission has higher execution risk (anti-bot, site variability) and lower per-session value than discovery. Discovery turns hours of manual URL collection into a single command. The brainstorm's Phase 1 explicitly precedes Phase 2 (assisted applications) for this reason.

## Problem Statement

### Current state

- `ingest-url <url>` fetches one known posting URL at a time. Greenhouse and Lever detail endpoints are implemented. Generic HTML fallback exists for other platforms. LinkedIn/Indeed hard-fail.
- The user must collect URLs by hand — scrolling LinkedIn/Indeed/Google, copy-pasting into a URLs file, then running `ingest-urls-file`. This is the single largest time sink in the current workflow and produces inconsistent coverage (easy to miss openings that appear between manual sweeps).
- `data/leads/` is populated only by things the user already found. There is no mechanism to surface new openings at target companies, no watchlist, no incremental cursor, and no way to dedupe across sources.
- From the brainstorm: "The system should support two families of sources: job boards where the user is already signed in; company career sites. Discovery output should be normalized into a single lead format so scoring and tracking do not care where the job came from." This has not been built.

### What changes

After batch 3:

- `python3 scripts/job_hunt.py discover-jobs` reads `config/watchlist.yaml`, polls every configured source, dedupes against existing leads, filters by keyword/location/seniority, and writes new leads via the same canonical path as `ingest-url`.
- A persisted cursor (`data/discovery/state.json`) tracks the last successful run per `(company, source)` tuple, so subsequent runs skip already-seen listings.
- Each run produces a structured JSON result and a run artifact in `data/discovery/history/<timestamp>.json` for audit.
- The user goes from "I need to find URLs" to "the repo finds URLs for me on the companies I care about" — turning the job search from push (I hunt) to pull (I review what the system surfaced).

## Proposed Solution

### High-level architecture

```
config/watchlist.yaml  (gitignored — contains PII-adjacent targeting data)
        │
        ▼
┌──────────────────────────────────────────────┐
│  discover-jobs CLI (core.py lazy-imports     │
│  discovery.discover_jobs)                    │
└─────────────┬────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────┐
│  src/job_hunt/discovery.py  +  watchlist.py  │
│  ┌──────────┬──────────┬──────────────────┐  │
│  │Greenhouse│  Lever   │  Generic careers │  │
│  │ listing  │ listing  │  crawler:         │  │
│  │ API      │ API      │  (1) JSON-LD     │  │
│  │          │          │  (2) ATS subdom. │  │
│  │          │          │  (3) regex       │  │
│  └────┬─────┴────┬─────┴────────┬─────────┘  │
│       │          │              │             │
│       └──────────┼──────────────┘             │
│                  ▼                             │
│     Dedup + filter + budget gate              │
│     (all three use DomainRateLimiter and      │
│      RobotsCache from net_policy.py — v3)     │
│                  │                             │
│                  ▼                             │
│       For each surviving listing:             │
│       ingest_url(posting_url)   ◄─── reuses   │
│                  │              batch 2       │
│                  ▼              infrastructure │
│       data/leads/<lead_id>.json                │
│                  │                             │
│                  ▼                             │
│       (AFTER all ingest complete)              │
│       Batched score_lead w/ ThreadPool         │
│       keyed on LLM rate profile, not          │
│       network rate profile                    │
└──────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────┐
│  data/discovery/history/<timestamp>.json     │
│  data/discovery/state.json  (cursor, schema-  │
│     versioned, atomic write)                 │
│  data/discovery/review/<entry_id>.{md,json}  │
│     (paired; .json is agent-consumable)      │
│  data/discovery/robots_cache.json            │
│     (persistent 24h TTL)                     │
└──────────────────────────────────────────────┘
```

All HTTP fetches go through batch 2's `ingestion.fetch()` (promoted from `_fetch`) and SSRF guards — **no new HTTP code path is introduced**. The existing 10s timeout, 2MB response cap (per-posting), private-IP blocking, redirect re-validation, decompression bomb cap, and LinkedIn/Indeed hard-fail all apply automatically. Batch 3 adds one new constant: `MAX_LISTING_BYTES = 8_000_000` (for Greenhouse/Lever listing endpoints only) with `MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000` paired with it. **Batch 3 also hardens batch 2's `_fetch` to pin the validated IP and send `Host:` header** (closes DNS-rebinding TOCTOU — applies to ALL callers, not just discovery).

### New files

- `src/job_hunt/discovery.py` — board listing fetchers, career-page crawler (JSON-LD + ATS + regex), orchestration entry point, `DiscoveryConfig`/`DiscoveryError`/`DiscoveryResult`/`Outcome`/`SourceRun`/`ListingEntry` types
- `src/job_hunt/watchlist.py` — YAML loader, filter predicates, schema validation, watchlist CRUD helpers with safe-escape emitter
- `src/job_hunt/net_policy.py` — `DomainRateLimiter`, `RobotsCache`, `registered_domain`, `KNOWN_SHARED_DOMAINS` (split from `utils.py` per todo #037)
- `config/watchlist.yaml` — gitignored; populated by user
- `config/watchlist.example.yaml` — tracked template
- `schemas/watchlist.schema.json` — watchlist config shape (includes `name` path-traversal regex)
- `schemas/discovery-cursor.schema.json` — `state.json` shape (versioned; `|`-separated keys)
- `schemas/discovery-run.schema.json` — run artifact shape, concretely specified against `DiscoveryResult.to_dict()`
- `schemas/discovery-review.schema.json` — frontmatter shape for single-file review entries (includes `entry_id` regex)
- `prompts/discovery/career-crawl.md` — agent guidance for reviewing `data/discovery/review/`
- `docs/guides/job-discovery.md` — user guide

### Modified files

- `src/job_hunt/ingestion.py` — (1) rename `_fetch` → `fetch` (no alias; update all in-repo call sites in same PR per todo #037); (2) expand `fetch` return to include response status + headers (tuple or dataclass — see Module structure); (3) add `max_decompressed_bytes` kwarg; (4) add `_PinnedHTTPSConnection` + `_pinned_opener` factory that pins the validated IP, sets `Host:` header, uses `server_hostname=hostname` for SNI, `check_hostname=True`, `CERT_REQUIRED`, `Connection: close`; (5) `_StrictRedirectHandler` re-pins on every hop via fresh `_validate_url_for_fetch`; (6) make `IngestionError` subclass `StructuredError` (new base in `utils.py`); (7) expose `HARD_FAIL_URL_PATTERNS`, `canonicalize_url`, regex patterns as public names (no underscore).
- `src/job_hunt/utils.py` — (1) upgrade `write_json` to `tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)` + fsync file AND parent directory; (2) add `StructuredError` base class (used by IngestionError/PdfExportError/DiscoveryError).
- `src/job_hunt/pdf_export.py` — `PdfExportError` subclasses `StructuredError`.
- `src/job_hunt/core.py` — register 11 new subparsers (`discover-jobs`, `discovery-state`, `watchlist-show`, `watchlist-add`, `watchlist-remove`, `watchlist-validate`, `review-list`, `review-promote`, `review-dismiss`, `robots-cache-clear`, `--reset-cursor` as flag on `discover-jobs`); all use lazy imports; uniform error handler catches `StructuredError`.
- `src/job_hunt/simple_yaml.py` — extend to support list-of-mappings at nesting depth 2 (reader); add `_emit_watchlist_yaml()` (writer) with input escaping and comment-loss detection (warn + require `--force`).
- `src/job_hunt/tracking.py` — `check_integrity` learns five new orphan types: stale `_intake/pending/` (>1h), orphaned `data/discovery/history/` run artifacts, stale `data/discovery/review/` (>30d), stale `.tmp` in any `data/` subdir, `status: discovered` leads >1h old missing `fit_assessment`.
- `schemas/lead.schema.json` — add optional `discovered_via` array with `source`, `company`, `discovered_at`, optional `listing_updated_at`, `confidence` enum (`high` | `weak_inference`).
- `.gitignore` — add `data/discovery/` and `config/watchlist.yaml`.
- `AGENTS.md` — add "Discovery Guardrails" section + "Schema versioning convention" subsection (long-lived state uses integer `schema_version` const; per-run artifacts don't).
- `README.md` — Quick Start adds `discover-jobs` step.
- `docs/profile/README.md` — cross-link.
- `tests/test_pipeline.py` + new `tests/test_discovery.py`, `tests/test_watchlist.py`, `tests/test_net_policy.py` (covers rate limiter + robots cache).

## Technical Approach

### Architecture decisions

**Reuse, don't re-implement.** Every HTTP request goes through `ingestion.fetch()`. Every lead write goes through `ingestion.ingest_url()` which delegates to `core.extract_lead()`. The discovery module contributes **orchestration and URL generation only**. This keeps SSRF posture, decompression safety, prompt-injection defense, intake lifecycle, and canonicalization in one place.

**Shared infrastructure goes in `net_policy.py`** (per v3 architecture review — todo #037). `DomainRateLimiter` and `RobotsCache` own persistent state and are not pure utilities; keeping `utils.py` as primitives-only (write_json, now_iso, slugify, short_hash, plus the new `StructuredError` base) keeps modules cohesive. Both `ingest_url` (batch 2) and `discover_jobs` (batch 3) import from `net_policy.py`.

**Per-domain rate limiting is global AND reserve-first.** If three threads independently poll three companies that all host on `greenhouse.io`, per-thread sleep is insufficient politeness. `DomainRateLimiter` uses a `threading.Lock`, keyed by eTLD+1 (e.g., `greenhouse.io`, `lever.co`, `example.com`), and the `acquire()` method reserves the next slot BEFORE releasing the lock to sleep — preventing the herd-awakening race.

**Filter scope: title + location only.** Board listing endpoints return `title`, `location`, and `absolute_url` — no description. Filtering on description would require a second fetch per listing (fan-out explosion). Description-aware filtering is what `score_lead` already does after ingestion. This is a deliberate separation of "cheap metadata filter" from "full fit scoring."

**Careers-page crawl: three-signal resolution ladder.**

1. **JSON-LD `JobPosting`** — parse `<script type="application/ld+json">`, filter for `@type == "JobPosting"`. Wins when present: structured, canonical, license-clean (Google For Jobs published the spec for machine parsing). High-confidence single-source-of-truth.
2. **ATS subdomain link discovery** — if the careers page links to `boards.greenhouse.io/X`, `jobs.lever.co/X`, `jobs.ashbyhq.com/X`, or a `*.myworkdayjobs.com` tenant, skip generic HTML extraction entirely and hit the platform-native API (Greenhouse/Lever today; others fall through to generic for now). High-confidence.
3. **Heuristic regex fallback** — require ≥2 independent signals: (a) href matches a career path hint, (b) anchor text matches role-word regex, (c) link is in `<footer>`/`<nav>`. Single-signal hits go to `data/discovery/review/` for human triage with a companion `.json` for agent consumption.

**Auto-scoring is batched AND recovers unscored leads on re-run** (v3 — todo #033). Discovery completes all ingestion first, then runs batched `score_lead` in a separate `ThreadPoolExecutor` (concurrency configurable via `--score-concurrency`, default 3). Critically, the scoring phase scans `data/leads/*.json` for ANY lead with `status: discovered` AND no `fit_assessment` — not just the freshly-discovered ones. A crash mid-batch leaves partial results; the next `discover-jobs` run automatically heals. `check-integrity` warns on `status: discovered` leads without `fit_assessment` older than 1h. `--no-score` opts out entirely.

**Review files are single `.md` with YAML frontmatter** (v3 — todo #035). Earlier drafts used a paired `.md` + `.json` — three reviewers converged on collapsing to one file. YAML frontmatter (parsed by `simple_yaml`) carries every structured field (`entry_id`, `candidate_url`, `anchor_text`, `signals`, `status`, `watchlist_company`, `discovered_at`, `DATA_NOT_INSTRUCTIONS: true` flag). Body is nonce-fenced human-readable narrative with HTML-escaped anchor text. Agents parse the frontmatter; humans read the body. No orphan class.

**Prompt-injection defense for review files uses nonce-fencing** (v3 — todo #039), mirroring batch 2's `_wrap_fetched_content` pattern. The fence is `\`\`\`untrusted_data_<secrets.token_hex(6)>` rather than fixed backticks; attacker-controlled anchor text containing stray ` ``` ` cannot escape because the nonce is unguessable. The `DATA_NOT_INSTRUCTIONS` frontmatter flag is a secondary defense (a capable attacker can override banners, but combined with nonce-fencing + schema validation the surface is closed).

**Anti-bot detection requires HTTP status AND pattern** (v3 — todo #039). `_detect_anti_bot(status, headers, body)` returns `True` only when status is 403/503 AND (`cf-ray` header present OR `<title>Just a moment` present). Body-regex-alone is both DoS-able (benign "protected by Cloudflare" disclosures) and bypassable (attacker-injected headers in attacker-controlled responses). This requires `fetch()` return type to include status + headers, not just body text — see Module structure.

**Dedup is layered.** (1) Same canonical URL within a run → `duplicate_within_run`, AND `discovered_via` on the existing lead is appended under a per-lead lock. (2) Canonical URL or fingerprint present in `data/leads/*.json` → `already_known`, AND `discovered_via` is appended on the existing lead. (3) `keywords_none` match → `filtered_out`. (4) `keywords_any` / `locations_any` / `seniority_any` miss → `filtered_out`. (5) Robots disallow → `skipped_by_robots`. (6) `--max-ingest` budget exceeded → `skipped_by_budget`. (7) Fetch/parse error → `failed`. Non-overlapping. Precedence explicit: `failed` > `skipped_by_robots` > `skipped_by_budget` > `filtered_out` > `duplicate_within_run` > `already_known` > `discovered`.

**Discovery does NOT invoke `build-draft`.** Discovery stops at "lead is in `data/leads/` with fit score attached." Draft generation remains an explicit, reviewed step.

**Config-tracking convention deviation.** `config/watchlist.yaml` is `.gitignore`d; `config/watchlist.example.yaml` is tracked. This is the first config file to deviate from the batch-1/batch-2 "all config tracked" convention. Justified because the watchlist contains user-specific target-company names (PII-adjacent). Documented in `AGENTS.md` and `docs/guides/job-discovery.md` so future sensitive configs follow the pattern.

### Module structure

```python
# src/job_hunt/utils.py  (additions — rest of module unchanged)

"""Shared primitives. New in v3: StructuredError base class; write_json upgraded
for concurrent same-path writers + parent-directory fsync.

Stateful cross-module infrastructure (rate limiter, robots cache) lives in
net_policy.py per v3 architecture review, NOT here.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import ClassVar


# =============================================================================
# StructuredError — base for all agent-consumable errors (todo #034)
# =============================================================================

class StructuredError(ValueError):
    """Base for IngestionError, PdfExportError, DiscoveryError.

    Provides a uniform shape for agent-consumable error handling:
    every subclass carries error_code / url / remediation and emits to_dict().
    CLI error handlers catch StructuredError once; no N-way except chain.
    """

    ALLOWED_ERROR_CODES: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        message: str,
        error_code: str,
        url: str = "",
        remediation: str = "",
    ):
        super().__init__(message)
        assert error_code in self.ALLOWED_ERROR_CODES, f"unknown error_code: {error_code}"
        self.error_code = error_code
        self.url = url
        self.remediation = remediation

    def to_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "url": self.url,
            "remediation": self.remediation,
        }


# =============================================================================
# Atomic JSON writes — unique tmp per call + parent-dir fsync (todos #030, #040)
# =============================================================================

def write_json(path: Path, payload: dict | list) -> None:
    """Atomically write JSON to path.

    v3 upgrade:
    - tempfile.mkstemp in TARGET DIRECTORY guarantees per-call unique tmp name
      (closes batch 1/2's same-path tmp collision — todo #030).
    - parent-directory fsync after os.replace for Linux ext4 durability
      (todo #040).
    - Startup sweep globs `*.tmp` to catch stragglers (todo #037).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        # Durability: fsync parent directory entry on Linux ext4.
        # No-op on Windows (os.open of dir fails); best-effort on macOS.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
```

```python
# src/job_hunt/net_policy.py  (NEW — v3 split from utils.py per todo #037)

"""Network-policy primitives: per-domain rate limiting + robots.txt cache.

Owns stateful cross-module infrastructure. utils.py stays primitives-only.
Both ingestion.py (batch 2) and discovery.py (batch 3) import from here.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .utils import write_json

logger = logging.getLogger(__name__)


# =============================================================================
# Registered-domain bucketing (v3 — hardened per todo #031)
# =============================================================================

KNOWN_SHARED_DOMAINS: Final = frozenset({
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com",
    "smartrecruiters.com", "recruitee.com", "personio.de", "personio.com",
})


def registered_domain(url: str) -> str:
    """Return eTLD+1 for rate-limit bucketing. Handles IPs, IDN, empty hosts.

    v3 edge cases (todo #031):
    - IP URLs (`http://1.2.3.4/`) — bucketed whole, not sliced (was returning "3.4")
    - Empty hostnames — raises ValueError (was returning "" and bucketing all)
    - IDN / Punycode — normalized via idna encoding
    """
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}")

    # Buckets IPs whole (no eTLD+1 slicing for IP literals)
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    # IDN normalization
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass  # use the raw lowercased host

    for known in KNOWN_SHARED_DOMAINS:
        if host == known or host.endswith(f".{known}"):
            return known

    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# =============================================================================
# Global per-domain rate limiter (reserve-first; thread-safe) — todo #031
# =============================================================================

@dataclass
class _DomainBudget:
    min_interval_s: float
    next_slot_at: float = 0.0


class DomainRateLimiter:
    """Thread-safe per-domain minimum-interval limiter.

    Reserve-first pattern: acquire() computes the next available slot and sets
    budget.next_slot_at = slot + interval WHILE holding the lock, then sleeps
    unlocked until that slot. Prevents thundering-herd race where N threads
    see the same "last request" time and all wake together.
    """

    def __init__(self, default_interval_s: float = 0.5):
        self._lock = threading.Lock()
        self._budgets: dict[str, _DomainBudget] = {}
        self._default = default_interval_s

    def set_interval(self, domain: str, seconds: float) -> None:
        with self._lock:
            budget = self._budgets.setdefault(domain, _DomainBudget(seconds))
            budget.min_interval_s = seconds

    def acquire(self, url: str) -> float:
        """Block until this domain's next slot. Returns seconds slept."""
        domain = registered_domain(url)
        with self._lock:
            budget = self._budgets.setdefault(
                domain, _DomainBudget(min_interval_s=self._default)
            )
            now = time.monotonic()
            slot = max(now, budget.next_slot_at)
            budget.next_slot_at = slot + budget.min_interval_s  # reserve
            wait = slot - now
        if wait > 0:
            time.sleep(wait)
        return wait


# =============================================================================
# Robots.txt cache — persistent, stampede-safe, poison-resistant (todo #040)
# =============================================================================

class RobotsCache:
    """Cache with differentiated TTLs persisted to disk. Stampede-safe.

    v3 hardening (todo #040):
    - Disallow-decision TTL = 1h (avoid long poisoning from compromised DNS).
    - Allow-decision TTL = 24h.
    - Stores resolved IP with each entry; invalidates on re-resolve mismatch.
    - `robots-cache-clear` CLI for manual flush.

    - Stampede-safe: per-host threading.Event coordinates in-flight fetches.
    - Spec-correct on 5xx: wraps urllib.robotparser (which inverts RFC 9309);
      5xx → "disallow, retry in TTL" not "allow all."
    - BOM-safe: strips leading BOM before parser.parse(lines).
    - Body cap: 500KB for robots.txt response.
    """

    _ROBOTS_MAX_BYTES: Final = 500_000
    _ALLOW_TTL_S: Final = 24 * 60 * 60
    _DISALLOW_TTL_S: Final = 60 * 60

    def __init__(
        self,
        cache_path: Path,
        rate_limiter: DomainRateLimiter,
        user_agent: str,
    ):
        self._cache_path = cache_path
        self._rate_limiter = rate_limiter
        self._user_agent = user_agent
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        # Cache entry: {fetched_at: iso, resolved_ip: str, rules: str, status: "allow"|"disallow"}
        self._cache: dict[str, dict] = self._load()

    def _load(self) -> dict: ...
    def _save(self) -> None: ...  # atomic via write_json

    def can_fetch(self, url: str) -> bool:
        """True if robots policy allows this URL for DISCOVERY_USER_AGENT.

        On cache miss: fetch once per host per TTL, coordinated via Event.
        On fetch error (5xx, DNS, network): spec-correct disallow with short TTL.
        """
        ...

    def clear(self) -> None:
        """Flush the entire cache — used by `robots-cache-clear` CLI."""
        ...
```

```python
# src/job_hunt/ingestion.py  (v3 additions per todos #028, #034, #037)

"""v3 changes:
- IngestionError subclasses utils.StructuredError (shared base; todo #034)
- _fetch renamed to fetch (no alias; in-repo only — todo #037)
- fetch returns FetchResult with status + headers + body (not just body — todo #039)
- _PinnedHTTPSConnection pins validated IP and preserves TLS integrity (todo #028)
- _StrictRedirectHandler re-pins on every hop (todo #028)
"""

import http.client
import socket
import ssl
from dataclasses import dataclass
from typing import Final

from .utils import StructuredError


INGESTION_ERROR_CODES: Final = frozenset({
    # ... batch 2 codes unchanged ...
})


class IngestionError(StructuredError):
    ALLOWED_ERROR_CODES = INGESTION_ERROR_CODES


@dataclass(frozen=True)
class FetchResult:
    """v3: fetch now returns status + headers + body, not body-only.

    Needed by anti-bot detection (status 403/503 + cf-ray header) and by
    anything else that needs to branch on HTTP metadata.
    """
    status: int
    headers: dict[str, str]  # case-insensitive keys, lower-cased
    body: str


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that CONNECTS to a pre-validated IP while preserving
    full TLS integrity (SNI, hostname verification, cert validation).

    Critical invariants:
    - socket connects to `self._pinned_ip`, NOT to `self.host` (defeats DNS rebind).
    - SSL wrap_socket sets `server_hostname=self.host` (SNI matches hostname).
    - ssl_context has check_hostname=True and verify_mode=CERT_REQUIRED.
    - HTTP Host: header carries self.host (not the IP) — set by http.client.

    `Connection: close` is set by caller to defeat pool-reuse attacks where a
    cached (host,port) connection bypasses this override.
    """

    def __init__(self, host, pinned_ip, port=443, context=None, timeout=None):
        super().__init__(host, port=port, timeout=timeout, context=context or ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(
            sock,
            server_hostname=self.host,  # SNI + cert CN/SAN validation target
        )


def _build_pinned_opener(pinned_ip: str):
    """Build a urllib opener whose HTTPSHandler uses _PinnedHTTPSConnection.

    Used by fetch() per-request. Each redirect re-validates via
    _validate_url_for_fetch and rebuilds a fresh opener with the new pin.
    """
    ...


def fetch(
    url: str,
    timeout: int = MAX_FETCH_TIMEOUT_S,
    max_bytes: int = MAX_FETCH_BYTES,
    max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
) -> FetchResult:
    """v3: returns FetchResult(status, headers, body).

    Each call validates the URL, resolves + pins the IP, builds a fresh pinned
    opener, fetches with Connection: close to defeat pool reuse, and returns
    the result. Redirects go through _StrictRedirectHandler which re-validates
    and re-pins each hop.

    Used by ingest_url (batch 2) AND discovery.discover_* (batch 3).
    """
    ...


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate AND re-pin on every redirect hop.

    v3: pin refresh (todo #028) — each redirect resolves fresh DNS, validates
    the new IP via _validate_url_for_fetch, and installs a new _PinnedHTTPSConnection
    for the next request.
    """
    max_redirections = 3
    ...
```

```python
# src/job_hunt/discovery.py  (v3)

"""Active job discovery — Greenhouse/Lever board APIs + generic careers crawl.

Batch 3 (2026-04-16, v3).

v3 highlights:
- DiscoveryError subclasses utils.StructuredError (todo #034)
- DiscoveryConfig dataclass replaces 12-param signature (todo #036)
- Review files: single .md with YAML frontmatter (todo #035)
- Anti-bot detection via status + header, not body alone (todo #039)
- Nonce-fenced review content prevents backtick-injection (todo #039)
- _append_discovered_via shape-checks + locks on lead_id (todo #030)
- Auto-scoring recovers unscored leads on re-run (todo #033)
- _LEAD_WRITE_LOCKS uses WeakValueDictionary (todo #040)
"""

from __future__ import annotations

import html
import json
import logging
import re
import secrets
import threading
import urllib.parse
import weakref
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Iterable, Literal

from .ingestion import (
    IngestionError, HARD_FAIL_URL_PATTERNS, canonicalize_url, fetch, FetchResult,
    GREENHOUSE_URL_RE, LEVER_URL_RE,
)
from .net_policy import DomainRateLimiter, RobotsCache, registered_domain
from .utils import (
    StructuredError, now_iso, read_json, slugify, short_hash, write_json,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

DISCOVERY_USER_AGENT: Final = "job-hunt/0.3"

MAX_LISTING_BYTES: Final = 8_000_000
MAX_LISTING_DECOMPRESSED_BYTES: Final = 20_000_000
MAX_WATCHLIST_COMPANIES: Final = 200
FETCH_CHAIN_TIMEOUT_S: Final = 20

# Input-validation regexes (todo #031)
COMPANY_NAME_RE: Final = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
ENTRY_ID_RE: Final = re.compile(r"^[a-f0-9]{16}$")

# Cursor key separator — intentionally NOT colon, which may appear in company
# names despite the regex (defense in depth). `|` is forbidden by COMPANY_NAME_RE.
CURSOR_KEY_SEPARATOR: Final = "|"

DISCOVERY_ERROR_CODES: Final = frozenset({
    "unknown_platform",
    "hard_fail_platform",
    "robots_fetch_failed",
    "watchlist_invalid",
    "watchlist_entry_exists",
    "watchlist_comments_present",   # v3 — todo #029
    "cursor_corrupt",
    "cursor_tuple_not_found",
    "review_entry_not_found",
    "anti_bot_blocked",
    "review_schema_invalid",
    "lead_write_race",              # v3 — todo #030
})


class DiscoveryError(StructuredError):
    """v3: subclasses the common StructuredError base (todo #034)."""
    ALLOWED_ERROR_CODES = DISCOVERY_ERROR_CODES


# CLI token → ListingEntry.source → discovered_via.source.enum
SOURCE_NAME_MAP: Final = {
    "greenhouse": ("greenhouse", "greenhouse_board"),
    "lever": ("lever", "lever_board"),
    "careers": ("careers_html", "careers_html"),
}


# =============================================================================
# Data types — explicit to_dict() per todo #032
# =============================================================================

Confidence = Literal["high", "weak_inference"]
Bucket = Literal[
    "discovered", "filtered_out", "duplicate_within_run", "already_known",
    "skipped_by_robots", "skipped_by_budget", "failed", "low_confidence",
]


@dataclass(frozen=True)
class ListingEntry:
    title: str
    location: str
    posting_url: str
    source: str
    source_company: str
    internal_id: str
    updated_at: str
    signals: tuple[str, ...] = ()    # v3: tuple for true immutability
    confidence: Confidence = "high"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "location": self.location,
            "posting_url": self.posting_url,
            "source": self.source,
            "source_company": self.source_company,
            "internal_id": self.internal_id,
            "updated_at": self.updated_at,
            "signals": list(self.signals),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Outcome:
    bucket: Bucket
    entry: ListingEntry | None
    detail: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "entry": self.entry.to_dict() if self.entry else None,
            "detail": dict(self.detail),
        }


@dataclass
class SourceRun:
    company: str
    source: str
    started_at: str
    completed: bool
    listing_truncated: bool
    budget_exhausted: bool
    entry_count: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class DiscoveryResult:
    outcomes: list[Outcome]
    sources_run: list[SourceRun]
    run_started_at: str
    run_completed_at: str

    def by_bucket(self, bucket: Bucket) -> list[Outcome]:
        return [o for o in self.outcomes if o.bucket == bucket]

    def to_dict(self) -> dict:
        """Concrete shape validated by schemas/discovery-run.schema.json."""
        return {
            "schema_version": 1,
            "run_started_at": self.run_started_at,
            "run_completed_at": self.run_completed_at,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "sources_run": [s.to_dict() for s in self.sources_run],
            "counts": {
                b: len(self.by_bucket(b)) for b in (
                    "discovered", "filtered_out", "duplicate_within_run",
                    "already_known", "skipped_by_robots", "skipped_by_budget",
                    "failed", "low_confidence",
                )
            },
        }


# =============================================================================
# DiscoveryConfig — trims 12-param signature per todo #036
# =============================================================================

@dataclass(frozen=True)
class DiscoveryConfig:
    max_ingest: int = 50
    max_workers: int = 3
    sources: tuple[str, ...] = ()            # empty = all
    dry_run: bool = False
    auto_score: bool = True
    score_concurrency: int = 3
    scoring_config: dict | None = None
    candidate_profile: dict | None = None
    reset_cursor: tuple[str, str] | None = None   # (company, source|"*")


# =============================================================================
# Anti-bot detection — status + header (not body alone) per todo #039
# =============================================================================

_CLOUDFLARE_TITLE_RE = re.compile(r"<title>\s*Just a moment", re.I)


def _detect_anti_bot(result: FetchResult) -> bool:
    """True iff response shows a bot-challenge. Requires BOTH status AND signal."""
    if result.status not in (403, 503):
        return False
    headers_lower = {k.lower() for k in result.headers}
    if "cf-ray" in headers_lower:
        return True
    if _CLOUDFLARE_TITLE_RE.search(result.body):
        return True
    return False


# =============================================================================
# Career-page crawler — JSON-LD first, then ATS subdomain, then heuristic
# =============================================================================

_CAREER_PATH_HINTS: Final = (
    "/careers", "/jobs", "/openings", "/join-us", "/work-with-us", "/opportunities",
)
_ROLE_WORD_RE = re.compile(
    r"\b(engineer|developer|scientist|manager|designer|analyst|architect|lead)\b",
    re.I,
)
_ATS_SUBDOMAIN_PATTERNS: Final = (
    re.compile(r"^https?://boards\.greenhouse\.io/([^/]+)/?$", re.I),
    re.compile(r"^https?://job-boards\.greenhouse\.io/([^/]+)/?$", re.I),
    re.compile(r"^https?://jobs\.lever\.co/([^/]+)/?$", re.I),
    re.compile(r"^https?://jobs\.ashbyhq\.com/([^/]+)/?$", re.I),
    re.compile(r"^https?://[^/]+\.myworkdayjobs\.com/", re.I),
)
_JSON_LD_RE = re.compile(
    r'<script\b[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.+?)</script>',
    re.I | re.S,
)


def _extract_jobpostings_from_jsonld(html_body: str) -> list[dict]: ...
def _detect_ats_subdomain_links(html_body: str, base_url: str) -> list[str]: ...
def _classify_heuristic_link(href: str, anchor_text: str, context: str) -> tuple[int, tuple[str, ...]]: ...
def discover_company_careers(
    domain: str,
    rate_limiter: DomainRateLimiter,
    robots: RobotsCache,
    watchlist_company: str,
) -> tuple[list[ListingEntry], list[dict]]: ...


# =============================================================================
# Nonce-fenced review file — single .md with YAML frontmatter (todo #035, #039)
# =============================================================================

def _write_review_entry(
    review_dir: Path,
    entry_id: str,
    candidate_url: str,
    anchor_text: str,
    signals: list[str],
    source_page: str,
    watchlist_company: str,
) -> Path:
    """Write one review entry as single .md with YAML frontmatter.

    v3 design (todo #035): no paired .json. Frontmatter carries every field
    the agent needs; body is human-readable nonce-fenced narrative.
    Frontmatter validated against schemas/discovery-review.schema.json.
    """
    if not ENTRY_ID_RE.match(entry_id):
        raise DiscoveryError(
            f"Invalid entry_id: {entry_id!r}",
            error_code="review_schema_invalid",
            remediation=f"entry_id must match {ENTRY_ID_RE.pattern}",
        )

    nonce = secrets.token_hex(6)
    fence_open = f"```untrusted_data_{nonce}"
    fence_close = "```"
    # Defensive: strip any stray occurrences of the close fence in input
    safe_anchor = anchor_text.replace(fence_close, fence_close.replace("`", "'"))
    safe_anchor = html.escape(safe_anchor)

    frontmatter = {
        "entry_id": entry_id,
        "DATA_NOT_INSTRUCTIONS": True,
        "candidate_url": candidate_url,
        "anchor_text_escaped": safe_anchor,
        "signals": signals,
        "source_page": source_page,
        "watchlist_company": watchlist_company,
        "discovered_at": now_iso(),
        "status": "pending",
        "fence_nonce": nonce,
    }
    # Emit YAML frontmatter + body; body is nonce-fenced human view
    ...


# =============================================================================
# Merge helpers — defensive append per todo #030
# =============================================================================

# WeakValueDictionary: unused locks get GC'd automatically (todo #040)
_LEAD_WRITE_LOCKS: "weakref.WeakValueDictionary[str, threading.Lock]" = weakref.WeakValueDictionary()
_LEAD_WRITE_LOCKS_LOCK = threading.Lock()


def _lock_for_lead(lead_id: str) -> threading.Lock:
    with _LEAD_WRITE_LOCKS_LOCK:
        lock = _LEAD_WRITE_LOCKS.get(lead_id)
        if lock is None:
            lock = threading.Lock()
            _LEAD_WRITE_LOCKS[lead_id] = lock
        return lock


def _append_discovered_via(
    lead_id: str,
    lead_path: Path,
    entry: ListingEntry,
    watchlist_company: str,
) -> dict:
    """Read-modify-write discovered_via under per-lead-id lock.

    v3 hardening (todo #030):
    - Lock key is lead_id (stable), NOT Path string (not canonical).
    - Shape check on existing discovered_via; non-list value → warn + reset.
    - Missing lead file raises DiscoveryError(lead_write_race) with remediation.
    """
    lock = _lock_for_lead(lead_id)
    with lock:
        try:
            lead = read_json(lead_path)
        except FileNotFoundError as exc:
            raise DiscoveryError(
                f"Lead file missing during provenance append: {lead_path}",
                error_code="lead_write_race",
                remediation="Re-run discover-jobs; within-run dedup should prevent this.",
            ) from exc

        existing = lead.get("discovered_via")
        if not isinstance(existing, list):
            logger.warning(
                "lead %s had non-list discovered_via (%r); resetting to []",
                lead_id, type(existing).__name__,
            )
            existing = []

        existing.append({
            "source": SOURCE_NAME_MAP[entry.source][1],
            "company": watchlist_company,
            "discovered_at": now_iso(),
            "listing_updated_at": entry.updated_at or None,
            "confidence": entry.confidence,
        })
        lead["discovered_via"] = existing
        write_json(lead_path, lead)
        return lead


# =============================================================================
# Orchestration — discover_jobs with DiscoveryConfig (todo #036)
# =============================================================================

def discover_jobs(
    watchlist_path: Path,
    leads_dir: Path,
    discovery_root: Path,
    config: DiscoveryConfig = DiscoveryConfig(),
) -> DiscoveryResult:
    """Poll every source, dedupe, filter, ingest, then batch-score with recovery.

    Cursor advances ONLY for complete, non-truncated, non-budget-capped sources.
    Scoring phase scans ALL data/leads/*.json for status=="discovered" AND
    missing fit_assessment — crashed mid-batch scoring heals on next run.

    Per-lead lock map uses WeakValueDictionary so unused locks are GC'd.
    """
    ...
```

```python
# src/job_hunt/watchlist.py  (v3 — safe write-back per todo #029)

"""Watchlist load, validate, filter predicates, and safe YAML write-back.

v3 write path (todo #029):
- All string inputs are double-quoted with escaped quotes/newlines/control chars.
- Control-character inputs rejected at CLI argument layer.
- On existing comments in target file: warn and require --force.
"""

# Rejected character classes for any user-supplied string at CLI boundary
_FORBIDDEN_INPUT_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_cli_string(value: str, field_name: str) -> str:
    """Reject control chars; used on --name, --notes, --greenhouse slug, etc."""
    if _FORBIDDEN_INPUT_CHARS_RE.search(value):
        raise DiscoveryError(
            f"Control character in {field_name!r}: {value!r}",
            error_code="watchlist_invalid",
            remediation="Remove newline/tab/control chars from input.",
        )
    return value


def _emit_watchlist_yaml(data: dict) -> str:
    """Safe YAML writer for watchlist config.

    Always double-quotes string values and escapes \\ " \\n \\r. Does NOT
    preserve comments (simple_yaml has no comment node) — caller must handle
    comment-loss warning.
    """
    ...


def watchlist_add(path: Path, entry: dict, force: bool = False) -> None:
    """Add an entry; fail if duplicate name; warn on comment loss unless --force.

    If target file contains comments, raise DiscoveryError(watchlist_comments_present)
    unless force=True. Write is via utils.write_json-style atomic tmp+replace.
    """
    ...


def watchlist_remove(path: Path, name: str, force: bool = False) -> None: ...
def watchlist_show(path: Path, company: str | None = None) -> dict: ...
def watchlist_validate(path: Path) -> dict: ...  # returns {valid, errors, warnings}
```


### Watchlist config format

Resolves the `simple_yaml` constraint by extending `simple_yaml` to support list-of-mappings at nesting depth 2 (a targeted addition, regression-tested against all existing YAML fixtures). The watchlist format stays natural YAML:

```yaml
# config/watchlist.yaml (gitignored)
#
# Companies to poll for new openings. Add/remove freely; re-read every run.
# Each entry needs at least ONE of greenhouse / lever / careers_url — otherwise
# it's skipped with a warning.

companies:
  - name: ExampleCo
    greenhouse: exampleco
    lever: exampleco
    careers_url: https://exampleco.com/careers
    notes: "primary target"
  - name: AnotherCorp
    lever: anothercorp
  - name: ThirdCo
    careers_url: https://thirdco.com/jobs
    notes: "generic crawl only"

filters:
  keywords_any:
    - engineer
    - developer
    - swe
  keywords_none:           # deal-breakers — highest precedence
    - clearance required
    - "ts/sci"
    - relocation required
    - principal
  locations_any:
    - remote
    - los angeles
    - orange county
    - san diego
  seniority_any:
    - senior
    - staff
    - "lead "
```

**Filter semantics (explicit and tested):**
- All match targets are lowercased + Unicode-normalized before comparison.
- Substring match (not token boundary).
- `keywords_any` / `keywords_none` match against `title + " " + location`.
- `locations_any` matches against `location` only.
- `seniority_any` matches against `title` only.
- Empty list = no constraint.
- Precedence: `keywords_none` (any match excludes) > `keywords_any` > `locations_any` > `seniority_any`.
- All four non-empty lists must be satisfied to pass.

Per-company overrides are deferred to batch 4. Not commented-out in the tracked example.

### CLI surface

**v1 commands (all ship in Phase 4/5):**

```bash
# Discovery
python3 scripts/job_hunt.py discover-jobs
python3 scripts/job_hunt.py discover-jobs --watchlist config/watchlist.yaml
python3 scripts/job_hunt.py discover-jobs --dry-run
python3 scripts/job_hunt.py discover-jobs --sources greenhouse,lever
python3 scripts/job_hunt.py discover-jobs --sources careers
python3 scripts/job_hunt.py discover-jobs --max-ingest 100
python3 scripts/job_hunt.py discover-jobs --no-score
python3 scripts/job_hunt.py discover-jobs --score-concurrency 5
python3 scripts/job_hunt.py discover-jobs --reset-cursor ExampleCo:greenhouse
python3 scripts/job_hunt.py discover-jobs --reset-cursor "ExampleCo:*"

# Introspection (v3 adds --last-run + --bucket per todo #038)
python3 scripts/job_hunt.py discovery-state
python3 scripts/job_hunt.py discovery-state --company ExampleCo
python3 scripts/job_hunt.py discovery-state --source greenhouse
python3 scripts/job_hunt.py discovery-state --last-run
python3 scripts/job_hunt.py discovery-state --last-run --bucket failed
python3 scripts/job_hunt.py discovery-state --last-run --bucket skipped_by_robots

# Watchlist CRUD (v3: --force for comment-loss override, watchlist-validate)
python3 scripts/job_hunt.py watchlist-show
python3 scripts/job_hunt.py watchlist-show --company ExampleCo
python3 scripts/job_hunt.py watchlist-add --name NewCo --greenhouse newco
python3 scripts/job_hunt.py watchlist-add --name NewCo --careers-url https://newco.com/careers
python3 scripts/job_hunt.py watchlist-add --name NewCo --greenhouse newco --force
python3 scripts/job_hunt.py watchlist-remove --name NewCo
python3 scripts/job_hunt.py watchlist-validate
python3 scripts/job_hunt.py watchlist-validate --watchlist config/watchlist-experimental.yaml

# Low-confidence review triage
python3 scripts/job_hunt.py review-list
python3 scripts/job_hunt.py review-list --status pending
python3 scripts/job_hunt.py review-promote <entry_id>     # ingests via ingest_url; entry_id regex-validated
python3 scripts/job_hunt.py review-dismiss <entry_id> --reason "..."

# Robots cache management (v3 — todo #040)
python3 scripts/job_hunt.py robots-cache-clear
```

All emit JSON to stdout matching their respective schemas.

**Deliberately NOT shipping (deferred):**
- `--since ISO` — the cursor already handles incremental runs. `--reset-cursor` covers rewind needs.

### Schema additions

**`schemas/lead.schema.json`** — one new optional field with `confidence`:

```json
{
  "discovered_via": {
    "type": "array",
    "items": {
      "type": "object",
      "required": ["source", "company", "discovered_at"],
      "properties": {
        "source": {
          "enum": ["greenhouse_board", "lever_board", "careers_html", "careers_html_review", "manual"]
        },
        "company": { "type": "string" },
        "discovered_at": { "type": "string" },
        "listing_updated_at": { "type": "string" },
        "confidence": { "enum": ["high", "weak_inference"] }
      }
    }
  }
}
```

`discovered_via` is NOT in `required`. The nested enum is closed today; expansion for `ashby_board`, `workday_board`, etc. is a batch-4 schema patch.

**`schemas/discovery-cursor.schema.json`** (new). v3: key separator is `|` (forbidden by `name` regex), enum dropped `partial` since the code never writes it (cursor advances only on complete runs; budget-capped/truncated leave cursor unchanged entirely).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "DiscoveryCursor",
  "type": "object",
  "required": ["schema_version", "entries"],
  "properties": {
    "schema_version": { "const": 1 },
    "entries": {
      "type": "object",
      "patternProperties": {
        "^[A-Za-z0-9 ._-]{1,64}\\|(greenhouse|lever|careers)$": {
          "type": "object",
          "required": ["last_run_at", "last_entry_count"],
          "properties": {
            "last_run_at": { "type": "string" },
            "last_entry_count": { "type": "integer" },
            "last_run_status": { "enum": ["complete", "failed"] }
          }
        }
      }
    }
  }
}
```

**Schema versioning convention** (v3 — new convention to document in AGENTS.md per todo #041): long-lived state files (cursors, caches) use `schema_version` as an integer `const` starting at 1. Per-run artifacts do not require versioning. Migration is via one-shot script OR delete-and-rescan when the artifact is rebuildable. Cursor IS rebuildable (delete-and-rescan) — migration script not required.

**`schemas/watchlist.schema.json`** (new). v3: `name` pattern enforces character class (todo #031 — prevents path traversal in filenames/cursor keys).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Watchlist",
  "type": "object",
  "required": ["companies"],
  "properties": {
    "companies": {
      "type": "array",
      "maxItems": 200,
      "items": {
        "type": "object",
        "required": ["name"],
        "properties": {
          "name": { "type": "string", "pattern": "^[A-Za-z0-9 ._-]{1,64}$" },
          "greenhouse": { "type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$" },
          "lever": { "type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$" },
          "careers_url": { "type": "string", "pattern": "^https://" },
          "notes": { "type": "string", "maxLength": 1000 }
        }
      }
    },
    "filters": { "type": "object" }
  }
}
```

**`schemas/discovery-run.schema.json`** (new). v3: authored as CONCRETE schema (not a forward reference to `to_dict()`). Matches the `DiscoveryResult.to_dict()` body shown in Module structure exactly — the schema and the code are both load-bearing specifications (todo #032).

**`schemas/discovery-review.schema.json`** (new). v3: single-file design (todo #035) — schema validates the YAML frontmatter of the review `.md`. `entry_id` regex enforced (todo #031). `DATA_NOT_INSTRUCTIONS: true` is a required literal.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "DiscoveryReviewEntry",
  "type": "object",
  "required": [
    "entry_id", "DATA_NOT_INSTRUCTIONS", "candidate_url", "anchor_text_escaped",
    "signals", "source_page", "watchlist_company", "discovered_at", "status", "fence_nonce"
  ],
  "properties": {
    "entry_id": { "type": "string", "pattern": "^[a-f0-9]{16}$" },
    "DATA_NOT_INSTRUCTIONS": { "const": true },
    "candidate_url": { "type": "string", "pattern": "^https?://" },
    "anchor_text_escaped": { "type": "string", "maxLength": 2000 },
    "signals": { "type": "array", "items": { "type": "string" } },
    "source_page": { "type": "string" },
    "watchlist_company": { "type": "string", "pattern": "^[A-Za-z0-9 ._-]{1,64}$" },
    "discovered_at": { "type": "string" },
    "status": { "enum": ["pending", "promoted", "dismissed"] },
    "fence_nonce": { "type": "string", "pattern": "^[a-f0-9]{12}$" }
  }
}
```

### Implementation Phases

#### Phase 1: Foundation (infrastructure, security fixes, parser extensions)

**Why first:** Every downstream phase depends on these. Also includes one P0 security fix affecting batch 2 (DNS-rebinding TOCTOU).

**Deliverables:**

- [x] **`utils.py`: `StructuredError` base class** (todo #034) — ancestor for `IngestionError`, `PdfExportError`, `DiscoveryError`. Uniform `error_code` / `url` / `remediation` / `to_dict()` contract.
- [x] **`utils.py`: upgrade `write_json`** (todos #030, #040):
  - Per-call unique tmp via `tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)`
  - fsync the file AND the parent directory (best-effort, platform-tolerant fallback)
  - `except BaseException` cleanup with `tmp_path.unlink(missing_ok=True)`
- [x] **`net_policy.py` new module** (todo #037) — hosts `DomainRateLimiter`, `RobotsCache`, `registered_domain`, `KNOWN_SHARED_DOMAINS`. Replaces the v2 "put them in utils.py" design.
- [x] **`DomainRateLimiter` reserve-first** — `acquire()` computes slot and updates `next_slot_at` inside the lock, sleeps unlocked. No thundering herd.
- [x] **`registered_domain()` hardened** (todo #031) — IP URLs bucketed whole; empty hostnames raise `ValueError`; IDN normalized via `idna` encoding.
- [x] **`RobotsCache` with differentiated TTLs** (todo #040):
  - Disallow-decision TTL = 1h (limits poison blast)
  - Allow-decision TTL = 24h
  - Stores resolved IP per entry; invalidates on re-resolve mismatch
  - Persistent at `data/discovery/robots_cache.json` (atomic via `write_json`)
  - Stampede-safe via per-host `threading.Event`
  - BOM-stripping; spec-correct 5xx → disallow
  - `clear()` method for `robots-cache-clear` CLI
- [x] **`ingestion.py`: rename `_fetch` → `fetch`** (todo #037) — no alias; update all in-repo call sites in same PR.
- [x] **`ingestion.py`: `FetchResult` dataclass** (todo #039) — `fetch()` now returns `FetchResult(status, headers, body)` not raw body string. Batch-2 call sites (ingest_url / detail endpoints / HTML fallback) updated to `.body` access.
- [x] **`ingestion.py`: `_PinnedHTTPSConnection`** (todo #028):
  - Subclass `http.client.HTTPSConnection`; override `connect()` to `socket.create_connection((pinned_ip, port))`.
  - `ssl.create_default_context()` with `check_hostname=True`, `verify_mode=CERT_REQUIRED`.
  - `wrap_socket(sock, server_hostname=self.host)` — SNI uses hostname, not IP.
  - `Host:` header carries hostname (default http.client behavior).
  - Caller sets `Connection: close` to defeat pool reuse.
- [x] **`ingestion.py`: `_StrictRedirectHandler` re-pins per hop** (todo #028) — each redirect re-validates via `_validate_url_for_fetch` and installs a fresh `_PinnedHTTPSConnection` for the next request.
- [x] **`ingestion.py`: `IngestionError(StructuredError)`** — retrofit to use shared base.
- [x] **`ingestion.py`: add `max_decompressed_bytes` kwarg**; `MAX_LISTING_DECOMPRESSED_BYTES = 20_000_000` used only by discovery's board fetchers.
- [x] **`ingestion.py`: IPv4-mapped-IPv6 explicit check** in `_validate_url_for_fetch` (`::ffff:127.0.0.1` → `private_ip_blocked`).
- [x] **`ingestion.py`: expose `HARD_FAIL_URL_PATTERNS`, `canonicalize_url`** as public (no underscore prefix).
- [x] **`pdf_export.py`: `PdfExportError(StructuredError)`** — retrofit to use shared base.
- [x] **`simple_yaml.py`: extend to support list-of-mappings at depth 2** (read path).
- [x] **`simple_yaml.py`: `_emit_watchlist_yaml()`** — safe writer with double-quoted strings, escaped `"` / `\\n` / `\\r` / control chars. Does NOT preserve comments — caller handles comment-loss warning.
- [x] **`watchlist.py`** — schema load + validation, `WatchlistFilters.passes()`, `validate_cli_string`, `watchlist_add / watchlist_remove / watchlist_show / watchlist_validate` with `--force` override for comment-loss (todo #029). Detects presence of comment lines in existing file and raises `DiscoveryError(watchlist_comments_present)` unless forced.
- [x] **Schemas**: `watchlist.schema.json` (name regex, maxItems: 200, HTTPS-only careers_url), `discovery-cursor.schema.json` (schema_version: 1, `|` separator, no `partial` enum), `discovery-review.schema.json` (entry_id regex, DATA_NOT_INSTRUCTIONS const).
- [x] `config/watchlist.example.yaml` (tracked template with comments).
- [x] `.gitignore`: add `config/watchlist.yaml` and `data/discovery/`.
- [x] `DISCOVERY_USER_AGENT: Final = "job-hunt/0.3"` constant (dropped non-resolving `+URL` — todo #043).

**Phase 1 tests (new files):**

- [x] `test_rate_limiter_serializes_same_domain` — 3 serial calls at 500ms interval take ≥1000ms wall time (slot reservations are 0, 500, 1000).
- [x] `test_rate_limiter_parallelizes_different_domains` — 2 calls on distinct domains complete in <100ms.
- [x] `test_rate_limiter_enforces_single_inflight_per_domain` — 5 threads × 10 calls against same domain; instrumented counter never exceeds 1.
- [x] `test_rate_limiter_no_thundering_herd` — 10 threads all call `acquire()` simultaneously; assert they complete at T=0, T=500ms, T=1000ms, ... not all at T=500ms.
- [x] `test_robots_fetch_5xx_disallows` — fixture serves 503; `can_fetch` returns False.
- [x] `test_robots_fetch_4xx_allows` — fixture serves 404; `can_fetch` returns True (except 401/403 which disallow).
- [x] `test_robots_disallow_specific_path` — honors `Disallow: /jobs/` for `DISCOVERY_USER_AGENT`.
- [x] `test_robots_stampede_prevention` — 10 threads `can_fetch` same new host; fixture server records exactly 1 robots.txt request.
- [x] `test_robots_persistent_cache_ttl` — write cache, re-instantiate, cached entries within 24h not re-fetched.
- [x] `test_robots_bom_handling` — robots.txt with leading `\ufeff` parses correctly.
- [x] `test_watchlist_loader_rejects_missing_name` — invalid config raises `DiscoveryError(watchlist_invalid)`.
- [x] `test_watchlist_loader_caps_companies_at_200`.
- [x] `test_filters_full_precedence_chain` — single entry that would fail at each level; assert reason matches topmost trigger.
- [x] `test_filters_empty_lists_are_no_op`.
- [x] `test_simple_yaml_parses_list_of_mappings` — the new depth-2 support.
- [x] `test_simple_yaml_rejects_depth_3_mappings` — explicit cap prevents future scope creep.
- [x] `test_simple_yaml_regression_existing_configs` — all batch 1/2 YAML files parse identically.
- [x] `test_write_json_concurrent_same_path` — 10 threads writing same target path; no file corruption, exactly one winner's content.
- [x] `test_write_json_fsyncs_parent_dir` (todo #040) — best-effort assertion; at minimum verify no exception under normal filesystems.
- [x] `test_startup_sweep_catches_mkstemp_stragglers` (todo #037) — seed `.foo.abc123.tmp` via mkstemp pattern; sweep warns on it.
- [x] `test_fetch_dns_rebinding_pinned_ip` (todo #028) — fake resolver returns safe IP first call, loopback second; `fetch()` actually connects to the safe IP; assert `sock.getpeername()` matches the pin.
- [x] `test_fetch_https_pinned_ip_cert_validates` (todo #028) — against a fixture-CA HTTPS endpoint; cert validation passes with `server_hostname=hostname` while connection IP is pinned.
- [x] `test_fetch_redirect_re_pins_ip` (todo #028) — first hop to host-A pins IP-A; redirect to host-B triggers fresh resolve + validate + pin IP-B; not a reuse of IP-A.
- [x] `test_fetch_connection_close_header_prevents_pool_reuse` (todo #028).
- [x] `test_fetch_ipv4_mapped_ipv6_blocked` — `::ffff:127.0.0.1` rejected with `private_ip_blocked`.
- [x] `test_registered_domain_ip_url` (todo #031) — `http://1.2.3.4/` buckets as `1.2.3.4`, not `3.4`.
- [x] `test_registered_domain_empty_hostname` (todo #031) — raises `ValueError`.
- [x] `test_registered_domain_idn` (todo #031) — Unicode hostname normalized via idna.
- [x] `test_structured_error_common_interface` (todo #034) — IngestionError, PdfExportError, DiscoveryError all expose identical API; `isinstance(X, StructuredError)` for each; CLI handler catches `StructuredError` uniformly.
- [x] `test_pdf_export_error_still_works` (todo #034) — batch-2 regression.
- [x] `test_discovery_user_agent_constant_single_sourced` (todo #041) — grep for `"job-hunt/"` in discovery.py / net_policy.py / utils.py finds only the constant definition.
- [x] `test_watchlist_name_rejects_path_traversal` (todo #031) — `name: "../../../../etc/passwd"` rejected by schema.
- [x] `test_watchlist_careers_url_rejects_http` — `careers_url: http://...` rejected at schema validate.
- [x] `test_watchlist_add_rejects_yaml_injection` (todo #029) — `--notes "evil: payload\\n- injected"` fails at CLI input validation.
- [x] `test_watchlist_add_control_chars_rejected` — `\\x00-\\x1f` in any input rejected.
- [x] `test_watchlist_add_warns_on_comment_loss` (todo #029) — existing file with comments + no `--force` → `DiscoveryError(watchlist_comments_present)`.
- [x] `test_watchlist_add_force_overrides_comment_warning` — with `--force` succeeds; comments replaced by regenerated structure.
- [x] `test_robots_cache_invalidates_on_resolved_ip_change` (todo #040) — cached entry with IP-X; current resolution returns IP-Y; cache invalidates.
- [x] `test_robots_cache_disallow_ttl_is_shorter` (todo #040) — disallow entry expires after 1h; allow entry after 24h.

**Phase 1 Acceptance:**

- [x] Every `raise DiscoveryError(...)` in discovery.py passes a code in `DISCOVERY_ERROR_CODES` (enforced by grep test).
- [x] `fetch` is the public name; no `_fetch` alias; all batch 1/2 call sites updated in the same PR.
- [x] `fetch` returns `FetchResult`; batch-2 call sites updated to use `.body`.
- [x] `StructuredError` base class used by all three structured error subclasses; uniform CLI handler.
- [x] `_PinnedHTTPSConnection` used for HTTPS; TLS integrity preserved (`server_hostname`, `check_hostname=True`, `CERT_REQUIRED`).
- [x] Redirects re-pin; validated by test.
- [x] `write_json` change doesn't break any existing test; parent-dir fsync is best-effort.
- [x] `simple_yaml` extension passes full existing-config regression.
- [x] `simple_yaml._emit_watchlist_yaml` escapes all user inputs; input validation at CLI layer rejects control characters.
- [x] `registered_domain` handles IP / IDN / empty edge cases.
- [x] `DomainRateLimiter` lives in `net_policy.py` (not `utils.py`).
- [x] `RobotsCache` uses differentiated TTLs (allow 24h, disallow 1h); invalidates on IP change.
- [x] IP-pinning fix validated end-to-end.

**Estimated effort:** 3 sessions (increased from 2 due to StructuredError retrofit, net_policy split, FetchResult type change, comment-loss handling, IP-pin TLS/redirect details).

#### Phase 2: Board listing fetchers (Greenhouse + Lever)

**Deliverables:**

- [x] `discover_greenhouse_board(company, rate_limiter) -> tuple[list[ListingEntry], bool]`
- [x] `discover_lever_board(company, rate_limiter) -> tuple[list[ListingEntry], bool]`
- [x] Lever `createdAt` ms-epoch → ISO-8601 conversion.
- [x] `MAX_LISTING_BYTES` + `MAX_LISTING_DECOMPRESSED_BYTES` respected; truncation flagged (not raised).
- [x] `ListingEntry` dataclass with `signals=[]` and `confidence="high"` defaults.
- [x] `SourceRun` dataclass for per-source accounting.
- [x] Test fixtures: `tests/fixtures/greenhouse-board-50-jobs.json`, `greenhouse-board-empty.json` (404 case), `greenhouse-board-truncated.json` (response exceeds MAX_LISTING_BYTES), `lever-board-20-postings.json`, `lever-board-500-error.json`.

**Phase 2 tests:**

- [x] `test_greenhouse_board_valid_slug_returns_entries`
- [x] `test_greenhouse_board_unknown_slug_returns_empty` (404 handled as empty, not raised)
- [x] `test_greenhouse_board_500_raises_ingestion_error`
- [x] `test_greenhouse_board_truncated_flags_listing_truncated`
- [x] `test_lever_board_valid_slug_returns_entries`
- [x] `test_lever_board_ms_epoch_converted_to_iso`
- [x] `test_listing_entries_have_valid_posting_urls` — every `posting_url` matches `GREENHOUSE_URL_RE` or `LEVER_URL_RE` so `ingest_url` accepts it unchanged.

**Phase 2 Acceptance:**

- [x] All `ListingEntry.posting_url` fields parseable by batch 2's existing `ingest_url` without new code.
- [x] `ListingEntry.signals` is `[]` for board-API entries (high confidence by virtue of the source, no individual signals listed).
- [x] `ListingEntry.confidence == "high"` for both Greenhouse and Lever.

**Estimated effort:** 1 session.

#### Phase 3: Generic career-page crawler

**Deliverables:**

- [x] `_extract_jobpostings_from_jsonld(html_body) -> list[dict]` — JSON-LD `JobPosting` extraction, tolerant of parse errors and `@type` arrays.
- [x] `_detect_ats_subdomain_links(html_body, base_url) -> list[str]` — returns ATS board URLs found in `<a href>`.
- [x] `_classify_heuristic_link(href, anchor_text, context) -> tuple[int, tuple[str, ...]]` — signal counter + labels (tuple, not list — todo #043).
- [x] `discover_company_careers(domain, rate_limiter, robots, watchlist_company) -> tuple[high_conf, low_conf]`
- [x] **Anti-bot detection via status + header** (v3, todo #039) — `_detect_anti_bot(FetchResult) -> bool` requires HTTP 403/503 AND (`cf-ray` header OR `<title>Just a moment`). On match raise `DiscoveryError(anti_bot_blocked)`.
- [x] LinkedIn/Indeed hard-fail check at entry (before robots or fetch).
- [x] HTTPS-only enforcement for `careers_url` (http rejected at schema validate).
- [x] **Review-file writer: single `.md` with YAML frontmatter** (v3, todo #035). Path: `data/discovery/review/<entry_id>.md`. `entry_id` matches `ENTRY_ID_RE = ^[a-f0-9]{16}$`. Frontmatter validated against `discovery-review.schema.json`. No paired `.json` file.
- [x] **Nonce-fenced review body** (v3, todo #039) — fence is `\`\`\`untrusted_data_<secrets.token_hex(6)>`; attacker-supplied ` ``` ` in anchor text cannot close the fence. Anchor text HTML-escaped before rendering.
- [x] **`DATA_NOT_INSTRUCTIONS: true` const** in YAML frontmatter as defense-in-depth banner.
- [x] Test fixtures: `careers-json-ld.html`, `careers-ats-subdomain.html`, `careers-heuristic-2signal.html`, `careers-heuristic-1signal.html`, `careers-anti-bot-403-cfray.html`, `careers-anti-bot-403-body-only.html` (negative case — status+body-only does NOT trigger), `careers-linkedin-url.html`, `careers-zero-signal.html`, `careers-role-word-no-path.html`.

**Phase 3 tests:**

- [x] `test_jsonld_jobposting_extracted` — fixture with `<script type="application/ld+json">` containing a JobPosting array.
- [x] `test_jsonld_jobposting_tolerates_malformed_json` — one good block + one malformed; good block still surfaces.
- [x] `test_ats_subdomain_greenhouse_detected` — link to `boards.greenhouse.io/foo` → caller hits Greenhouse API instead of parsing HTML.
- [x] `test_two_signal_link_classified_high_confidence`
- [x] `test_one_signal_link_written_to_review`
- [x] `test_zero_signal_link_discarded`
- [x] `test_anti_bot_requires_status_and_pattern_not_body_alone` (v3, todo #039) — body containing "cloudflare" but status 200 does NOT trigger; status 403 + `cf-ray` header DOES trigger; status 503 + `Just a moment` title DOES trigger.
- [x] `test_anti_bot_raises_structured_error` — `DiscoveryError(anti_bot_blocked)` with remediation text.
- [x] `test_linkedin_careers_url_hard_fails` — watchlist entry with `careers_url: https://linkedin.com/jobs/...` raises `DiscoveryError(hard_fail_platform)`.
- [x] `test_http_careers_url_rejected_at_load` — `careers_url: http://example.com/careers` fails watchlist schema validation.
- [x] `test_careers_crawler_uses_fetch_not_urllib_direct` — static analysis test: grep confirms no direct `urllib.request.urlopen` in discovery.py.
- [x] `test_robots_disallow_blocks_crawl` — robots disallows `/careers/`; crawler returns empty + adds to skipped_by_robots.
- [x] `test_review_file_single_file_with_frontmatter` (v3, todo #035) — one `.md` written; no `.json` written; frontmatter parses via simple_yaml; schema-valid.
- [x] `test_review_file_html_escapes_anchor_text` — injection-shaped anchor text appears escaped in the frontmatter `anchor_text_escaped` field.
- [x] `test_review_file_fence_resists_backtick_injection` (v3, todo #039) — anchor text containing ` ``` ` does NOT escape the nonce-fenced block (fence nonce is unique per entry).
- [x] `test_review_file_entry_id_regex_rejected` (todo #031) — `entry_id = "../evil"` raises `DiscoveryError(review_schema_invalid)`.

**Phase 3 Acceptance:**

- [x] Every high-confidence `ListingEntry` has `signals != ()` (for careers_html source) OR source == "greenhouse"|"lever" (implicit high confidence).
- [x] Every low-confidence entry lands in `data/discovery/review/` as a single `.md` with frontmatter, never in `data/leads/`.
- [x] Generic crawler never bypasses `ingestion.fetch` for HTTP I/O.
- [x] All user-agent strings reference `DISCOVERY_USER_AGENT` constant.
- [x] Anti-bot detection requires BOTH status AND header/title signal (not body-alone).
- [x] Review file fence uses per-entry nonce; escape of fence-closer is impossible without guessing nonce.

**Estimated effort:** 2 sessions.

#### Phase 4: Orchestration — discover_jobs + CLI surface

**Deliverables:**

- [x] **`DiscoveryConfig` dataclass** (v3, todo #036) — 11 tunable parameters; `discover_jobs(watchlist_path, leads_dir, discovery_root, config=DiscoveryConfig())` signature.
- [x] **`DiscoveryResult`, `Outcome`, `SourceRun`, `ListingEntry` with explicit `to_dict()` bodies** (v3, todo #032). No `...` placeholders. Concrete `schemas/discovery-run.schema.json` validates the result.
- [x] `discover_jobs()` main entry point.
- [x] **Per-lead lock map via `WeakValueDictionary`** (v3, todo #040) for `_append_discovered_via` — unused locks auto-GC'd.
- [x] **Lock key is `lead_id`** (v3, todo #030) — stable identity, not Path string.
- [x] **`_append_discovered_via` defensive merge** (v3, todo #030) — shape check on existing value (non-list → warn + reset); missing file raises `DiscoveryError(lead_write_race)`.
- [x] Dedup: one-time scan of `data/leads/*.json` at run start builds two sets: canonical URL and fingerprint.
- [x] Cursor schema-versioned (`schema_version: 1`, integer const). Atomic write via `write_json`. Key separator is `|` (v3, todo #031).
- [x] Cursor advancement invariant: advance ONLY IF `listing_truncated=False` AND `budget_exhausted=False` AND source completed. `last_run_status` only ever writes `complete` or `failed` (enum updated v3 — `partial` removed since it's never written).
- [x] Coalesced cursor writes: one `write_json` per company (not per source).
- [x] Run artifact written at `data/discovery/history/<timestamp>.json` validated against `discovery-run.schema.json` (concrete schema, v3).
- [x] `--max-ingest` budget gate; exhaustion sets `SourceRun.budget_exhausted=True` and marks source as not-complete.
- [x] Per-company ThreadPoolExecutor(max_workers=3); sources serial within a company.
- [x] **Batched scoring with crash recovery** (v3, todo #033) — after all ingestion completes, `score_lead` scans BOTH newly-discovered leads AND `data/leads/*.json` where `status == "discovered"` AND `fit_assessment` is missing. Runs in separate `ThreadPoolExecutor(max_workers=config.score_concurrency)`. `--no-score` skips entirely. A crashed mid-batch scoring phase heals on the next `discover-jobs` run automatically.
- [x] Startup sweep: log warnings for `_intake/pending/*.md` files older than 1 hour AND `data/discovery/review/*.md` older than 30 days AND stale `.tmp` files (glob `*.tmp` — catches both batch-2 and batch-3 mkstemp patterns per todo #037) in any `data/` subdirectory AND `status: discovered` leads >1h old missing `fit_assessment`.
- [x] **CLI subparsers (v3 full list — 11 new)**: `discover-jobs`, `discovery-state` (with `--last-run` and `--bucket` flags, todo #038), `watchlist-show`, `watchlist-add` (with `--force` for comment override, todo #029), `watchlist-remove`, `watchlist-validate` (todo #038), `review-list`, `review-promote`, `review-dismiss`, `robots-cache-clear` (todo #040). Plus `--reset-cursor` as flag on `discover-jobs`.
- [x] **All CLI dispatch uses lazy imports** — `from .discovery import X` or `from .watchlist import X` inside handler. Top-level `core.py` import time measured in regression test.
- [x] **Uniform CLI error handler** (v3, todo #034) — catches `StructuredError`; emits `exc.to_dict()` as stdout JSON for agent consumption.
- [x] `--reset-cursor COMPANY|SOURCE` and `--reset-cursor "COMPANY|*"` supported (v3 uses `|` separator, todo #031).
- [x] `review-promote <entry_id>` validates `entry_id` against `ENTRY_ID_RE` before filesystem access (v3, todo #031); runs `ingest_url` on the stored `candidate_url` (which re-validates through `_validate_url_for_fetch`); appends `discovered_via: careers_html_review`; updates the single review `.md` frontmatter `status: promoted`.
- [x] `review-dismiss <entry_id> --reason "..."` updates frontmatter `status: dismissed` + stores reason.
- [x] **`watchlist-add` safe YAML write** (v3, todo #029) — all string inputs pass `validate_cli_string` (rejects control chars); YAML emitted via `_emit_watchlist_yaml`; if existing file contains comments and `--force` not set, raises `DiscoveryError(watchlist_comments_present)`.

**Phase 4 tests:**

- [x] `test_discover_jobs_end_to_end_mixed_sources` — fixtures: ExampleCo (Greenhouse+careers), AnotherCorp (Lever). Assert bucket counts, `discovered_via` on new leads, cursor advances for complete sources only.
- [x] `test_discover_jobs_dedupes_across_sources` — same canonical URL from Greenhouse AND careers crawl → 1 lead, 2 `discovered_via` entries, second goes to duplicate_within_run bucket (but `discovered_via` still appended).
- [x] `test_discover_jobs_appends_discovered_via_on_already_known` — second-run surfaces same lead → `discovered_via` grows by one.
- [x] `test_discover_jobs_concurrent_discovered_via_append` (todo #030) — 3 threads append to same lead via different source tuples; final array has 3 entries; no lost writes.
- [x] `test_append_discovered_via_handles_malformed_existing` (todo #030) — lead with `discovered_via: {}` or `"string"` → warning logged, reset to `[]`, new entry appended.
- [x] `test_append_discovered_via_missing_file_raises_structured` (todo #030) — lead path doesn't exist → `DiscoveryError(lead_write_race)`.
- [x] `test_append_discovered_via_locks_on_lead_id_not_path` (todo #030) — `Path("a/b.json")` and `Path("./a/b.json")` serialize through the same lock.
- [x] `test_discover_jobs_idempotent_second_run`.
- [x] `test_discover_jobs_max_ingest_cursor_unchanged` — `--max-ingest 5` with 100 matching; cursor does NOT advance for budget-capped source.
- [x] `test_discover_jobs_listing_truncated_cursor_unchanged`.
- [x] `test_discover_jobs_dry_run_no_disk_writes`.
- [x] `test_discover_jobs_no_score_leaves_leads_unscored`.
- [x] `test_discover_jobs_auto_score_batched_not_inline` — `score_lead` called AFTER all `ingest_url` completes.
- [x] `test_discover_jobs_rescues_unscored_leads_on_next_run` (v3, todo #033) — seed `data/leads/` with one `status: discovered` lead missing `fit_assessment`; second run of `discover-jobs` re-scores it even though it's not freshly-discovered.
- [x] `test_discover_jobs_cursor_crash_recovery`.
- [x] `test_discovery_config_maps_from_argparse` (todo #036) — argparse namespace → `DiscoveryConfig` round-trip.
- [x] `test_discovery_result_to_dict_matches_schema` (todo #032) — jsonschema validate against `discovery-run.schema.json`.
- [x] `test_outcome_to_dict_all_buckets` (todo #032) — each Literal value serializes.
- [x] `test_discover_jobs_logs_warning_on_stale_intake`.
- [x] `test_reset_cursor_single_tuple` — `--reset-cursor "ExampleCo|greenhouse"`.
- [x] `test_reset_cursor_glob_company` — `--reset-cursor "ExampleCo|*"`.
- [x] `test_reset_cursor_not_found_raises`.
- [x] `test_discovery_state_emits_json`.
- [x] `test_discovery_state_last_run_emits_buckets` (v3, todo #038) — `--last-run` reads latest `data/discovery/history/*.json`.
- [x] `test_discovery_state_last_run_bucket_filter` (v3, todo #038) — `--last-run --bucket failed` returns only failed outcomes.
- [x] `test_watchlist_add_preserves_existing_entries`.
- [x] `test_watchlist_add_duplicate_name_raises` — `DiscoveryError(watchlist_entry_exists)`.
- [x] `test_watchlist_validate_emits_json` (v3, todo #038) — `{valid, errors, warnings}` shape.
- [x] `test_review_promote_ingests_and_updates_status`.
- [x] `test_review_promote_rejects_entry_id_traversal` (todo #031) — `entry_id = "../evil"` raises.
- [x] `test_review_promote_candidate_url_revalidates_ssrf` — stored URL pointing at loopback blocked via `_validate_url_for_fetch`.
- [x] `test_review_dismiss_records_reason`.
- [x] `test_review_entry_not_found_raises`.
- [x] `test_robots_cache_clear_flushes_cache` (todo #040).
- [x] `test_cli_error_handler_catches_structured_error_uniformly` (todo #034) — each of `IngestionError`, `DiscoveryError`, `PdfExportError` handled by the same `except StructuredError` branch.

**Phase 4 Acceptance:**

- [x] All 7 dedup buckets appear in run artifact JSON, even when empty.
- [x] `SOURCE_NAME_MAP` is the single source of truth; test asserts CLI tokens / `ListingEntry.source` / `discovered_via.source` enum values stay in sync.
- [x] Cursor file always validates against `discovery-cursor.schema.json`.
- [x] Run artifact always validates against `discovery-run.schema.json` (concrete schema).
- [x] Every lead written has `discovered_via` with ≥1 entry.
- [x] Every lead surfaced via `already_known` OR `duplicate_within_run` has its `discovered_via` appended (provenance never dropped).
- [x] CLI dispatch uses lazy imports; top-level `core.py` import time doesn't regress (measured).
- [x] Every new command emits JSON by default.
- [x] `--no-score` parses to `config.auto_score=False`.
- [x] CLI uniform error handler catches `StructuredError`.
- [x] Scoring phase heals crashed-mid-batch unscored leads on re-run.
- [x] Cursor keys use `|` separator; `name` regex ensures no collision.

**Estimated effort:** 4 sessions (up from 3 due to `DiscoveryConfig`, rescore-on-rerun, 3 new commands, `StructuredError` CLI handler unification, per-lead lock normalization).

#### Phase 5: Docs + integration + backward-compat hardening

**Deliverables:**

- [x] `docs/guides/job-discovery.md` — user guide: watchlist setup, filter semantics with 3 worked examples, cursor behavior, review triage workflow, LinkedIn/Indeed policy, troubleshooting (unknown-company 404, Cloudflare challenge, rate-limit observed behavior, stale cursor recovery), config-tracking convention deviation.
- [x] `prompts/discovery/career-crawl.md` — agent guidance for reading `data/discovery/review/<entry_id>.json` and deciding `review-promote` vs `review-dismiss`.
- [x] `AGENTS.md` — "Discovery Guardrails" section: rate limiting, robots discipline, LinkedIn/Indeed policy, intake sweep, `config/watchlist.yaml` gitignore convention, `DISCOVERY_ERROR_CODES` enumeration alongside `INGESTION_ERROR_CODES` / `PdfExportError` codes.
- [x] `README.md` — Quick Start adds "3. Discover jobs" step between normalize-profile and score-lead.
- [x] `docs/profile/README.md` — brief cross-link.
- [x] `tracking.check_integrity` extension: four new orphan types surfaced (stale `_intake/pending/*.md` >1h, orphaned `data/discovery/history/*.json`, stale `data/discovery/review/*.md` >30d, stale `.tmp` files anywhere in `data/`).
- [x] Backward-compat regression test (expanded): load a batch-1 lead fixture missing `discovered_via`, `canonical_url`, `ingestion_method`, `ingested_at`; assert (a) `jsonschema.validate` passes, (b) `score_lead` succeeds, (c) `ats_check` succeeds, (d) `apps_dashboard` aggregator includes it, (e) `analyze_skills_gap` handles it, (f) `check_integrity` reports no false orphans.
- [x] End-to-end `test_batch3_end_to_end`: watchlist setup → `discover-jobs` run → lead files exist with `discovered_via` populated → `apps-dashboard` still works → `check-integrity` passes → batch-2 end-to-end test still passes.
- [x] All 156 batch-2 tests continue to pass unchanged.

**Phase 5 Acceptance:**

- [x] 156 batch-2 tests pass.
- [x] New test count: ≥45 (Phase 1: ~17, Phase 2: ~7, Phase 3: ~12, Phase 4: ~20, Phase 5: ~5 integration).
- [x] `AGENTS.md` includes Discovery Guardrails section with full `DISCOVERY_ERROR_CODES` list.
- [x] `docs/guides/job-discovery.md` passes a basic structural check (includes sections: setup, filter semantics, cursor, review triage, troubleshooting).
- [x] `check-integrity` detects all 4 new orphan types (validated via fixtures).

**Estimated effort:** 1 session.

**Total estimated effort:** 11 sessions (up from v2's 9, v1's 6-7). The v3 additions absorb:
- Phase 1: +1 session for `StructuredError` retrofit, `net_policy.py` split, `FetchResult` type change, IP-pin TLS/redirect specifics, comment-loss handling, 10 new tests.
- Phase 4: +1 session for `DiscoveryConfig`, rescore-on-rerun, 3 new commands (`watchlist-validate`, `robots-cache-clear`, `--last-run/--bucket`), `StructuredError` unified CLI handler, 8 new tests.

## Alternative Approaches Considered

### A. Make `ingest-url` smarter (no separate discovery module)

Teach `ingest_url` to detect board-root URLs and fan out.

**Rejected:** `ingest_url` is 1:1 "URL → one lead." Changing it to optionally return N leads breaks the return-type contract. A separate `discovery.py` module keeps `ingest_url` pure and makes batch 3 additive.

### B. Scheduled cron daemon instead of on-demand CLI

**Rejected:** zero operational complexity. A cron line is the user's call, not the tool's.

### C. Use a third-party library for career crawling (BeautifulSoup, selectolax)

**Rejected:** "no new default dependencies" line holds. JSON-LD parsing uses `json.loads`, heuristic matching uses `re`. If heuristics prove insufficient, `beautifulsoup4` is a candidate `[careers]` optional extra in a follow-up batch (same pattern as batch 2's `[pdf]`).

### D. Switch to `protego` for RFC-9309-compliant robots parsing

**Rejected:** no deps. Document stdlib `robotparser` limitations and wrap them (5xx handling, BOM, explicit longest-match notes in the guide).

### E. Include LinkedIn/Indeed scraping now

**Rejected:** batch 2 hard-failed these for good reasons (login walls, aggressive bot detection, TOS, legal risk). Needs headless browser automation + residential proxies — blast radius larger than the rest of batch 3 combined.

### F. Cut the generic career crawler (simplicity pressure)

**Rejected:** user explicitly asked for web scraping beyond known URLs; research shows JSON-LD + ATS-subdomain signals give the crawler high precision (not the false-positive pit the original plan feared); Greenhouse/Lever-only would miss most target companies.

### G. Include outreach draft generator

**Rejected:** separate content-generation vertical with its own prompts and provenance semantics. Batch 3 stays focused on discovery. Batch 4 P0 candidate.

### H. Include scoring calibration

**Rejected:** needs ≥30 applications of data. Premature.

### I. Collapse 7 dedup buckets → 3 (simplicity pressure)

**Rejected:** each bucket drives distinct agent behavior. Hiding `duplicate_within_run` inside `already_known` would lose a legitimate UX signal.

### J. Drop `_DomainRateLimiter` class for inline `time.sleep(0.5)` (simplicity pressure)

**Rejected:** generic crawler targets arbitrary user-supplied domains; a single lock on a single sleep serializes unrelated hosts. Global per-domain limiter is ~60 LOC and needed.

## System-Wide Impact

### Interaction Graph

```
scripts/job_hunt.py
  → core.main()   [lazy: from .discovery import discover_jobs]
  → discovery.discover_jobs(watchlist_path, leads_dir, discovery_root, ...)
      │
      ├─ watchlist.load_watchlist(path)
      │     └─ simple_yaml.loads(text)  [EXTENDED: list-of-mappings at depth 2]
      │     └─ schema_checks.validate(data, watchlist.schema.json)
      │
      ├─ cursor = _load_cursor(discovery_root / "state.json")
      │     └─ schema_checks.validate(cursor, discovery-cursor.schema.json)
      │     └─ on failure: raise DiscoveryError(cursor_corrupt)
      │
      ├─ existing_urls, existing_fingerprints = _scan_existing_leads(leads_dir)
      │
      ├─ rate_limiter = DomainRateLimiter(default_interval_s=0.5)   [utils.py]
      ├─ robots = RobotsCache(discovery_root/robots_cache.json, rate_limiter, DISCOVERY_USER_AGENT)
      │
      ├─ ThreadPoolExecutor(max_workers=3) per company:
      │     For each configured source (greenhouse/lever/careers) SERIALLY:
      │         ├─ rate_limiter.acquire(domain)  [reserve-first; sleep unlocked]
      │         ├─ IF source == careers: robots.can_fetch(url)
      │         │                        [OR anti_bot_blocked detection]
      │         ├─ discover_{source}(...)  [via ingestion.fetch()]
      │         │   ingestion.fetch() does:
      │         │     ├─ _validate_url_for_fetch(url)  [SSRF; IP-pin]
      │         │     ├─ HTTP GET via opener w/ _StrictRedirectHandler
      │         │     ├─ IP-pinned HTTPConnection (batch-3 Phase 1 fix)
      │         │     └─ _decompress_bounded(body, limit=max_decompressed_bytes)
      │         ├─ For each ListingEntry:
      │         │     ├─ watchlist.filters.passes(entry)   [→ filtered_out]
      │         │     ├─ canonical = canonicalize_url(entry.posting_url)
      │         │     ├─ canonical in within_run_seen?     [→ duplicate_within_run
      │         │     │                                      + _append_discovered_via]
      │         │     ├─ canonical in existing_urls?       [→ already_known
      │         │     │                                      + _append_discovered_via]
      │         │     ├─ budget exhausted?                 [→ skipped_by_budget,
      │         │     │                                      mark source incomplete]
      │         │     └─ ingest_url(entry.posting_url)
      │         │           ├─ _validate_url_for_fetch()
      │         │           ├─ fetch()  [batch 2, IP-pinned]
      │         │           ├─ _wrap_fetched_content()
      │         │           ├─ intake lifecycle: _intake/pending/ → processed/
      │         │           └─ write_json(data/leads/{lead_id}.json)
      │         │                 [utils.write_json: unique tmp via mkstemp]
      │         └─ Coalesced cursor write: one write_json per company
      │               (NOT per-source; reduces 150 writes → 50 at 50-company scale)
      │
      ├─ [after all ingestion complete]
      ├─ IF auto_score: ThreadPoolExecutor(max_workers=score_concurrency):
      │     For each newly-discovered lead:
      │         score_lead(lead, profile, scoring_config)
      │         write_json(data/leads/{lead_id}.json)   [rate-limiter-independent]
      │
      └─ _write_run_artifact(discovery_root/history/<ts>.json, DiscoveryResult.to_dict())
```

### Error & Failure Propagation

- `IngestionError` from `fetch()` → caught in discovery per-listing handler → `Outcome(bucket="failed", detail=error.to_dict())`, per-source continues.
- `DiscoveryError` from discovery-specific conditions → same failure bucket.
- `FileNotFoundError` on cursor load → raise `DiscoveryError(cursor_corrupt)` (vs batch-1 KeyError drift).
- Schema validation failure on watchlist YAML → immediate raise `DiscoveryError(watchlist_invalid)`, no partial run.
- Per-source fetch failure does NOT advance cursor for that (company, source) pair.
- Anti-bot detection (Cloudflare challenge page) → `DiscoveryError(anti_bot_blocked)` bubbles up to failed bucket; host marked as "will likely need manual ingestion."

### State Lifecycle Risks

- **Partial run → cursor only advances for complete, non-budget-capped, non-truncated sources.** Integration Test 6 now codifies this (reversed from original buggy spec).
- **Batch 2 intake lifecycle inherited.** Batch 3 adds startup sweep-and-warn for pending files >1h old.
- **Concurrent writers to `data/leads/`.** `write_json` upgraded to per-call unique tmp. Per-lead lock map serializes `discovered_via` appends. Within-run dedup set guarded by `_LEAD_WRITE_LOCKS_LOCK`.
- **Review directory accumulation.** `check-integrity` extension warns on entries >30 days old; `review-dismiss` explicitly clears.
- **Cursor unbounded growth.** Acknowledged; batch 4 candidate for `cleanup-cursor` subcommand. Watchlist load warns on cursor entries for companies no longer in watchlist (cursor drift signal).
- **robots_cache.json unbounded growth.** 24h TTL means entries self-expire; file is a cache, safe to delete at any time.

### API Surface Parity

Batch 2 and batch 3 produce leads of the same shape. New optional field (`discovered_via`) is additive and consumed via `.get("discovered_via", [])` by all readers. Any downstream reader (`score_lead`, `build_draft`, `apps-dashboard`, `analyze-skills-gap`, `analyze-rejections`, `check-integrity`, `ats-check`) works identically regardless of lead origin.

`check-integrity` learns four new orphan types:
- Stale `_intake/pending/*.md` (>1h).
- Orphaned `data/discovery/history/<ts>.json` (referenced leads no longer exist — this is OK; flagged as info, not error).
- Stale `data/discovery/review/*.md` (>30d).
- Stale `.tmp` files anywhere under `data/`.

### Integration Test Scenarios

1. **Mixed-sources end-to-end.**
2. **Cross-source dedup produces 1 lead with 2 `discovered_via` entries.**
3. **Idempotent second run.**
4. **Rate-limit verification: 3 Greenhouse slugs wall time ≥1000ms.**
5. **Robots disallow → skipped.**
6. **Max-ingest budget does NOT advance cursor.** (Reversed from original plan.)
7. **Listing-truncation does NOT advance cursor.**
8. **Crash between lead write and cursor write — next run dedupes, no double-ingest.**
9. **Concurrent `discovered_via` appends serialize correctly (3 threads → 3 entries in order).**
10. **Batched scoring runs AFTER all ingestion.**
11. **Anti-bot challenge detection → `anti_bot_blocked` error surfaced.**
12. **JSON-LD `JobPosting` extraction preferred over regex fallback.**
13. **DNS-rebinding test: validated IP stays pinned across attacker flip.**
14. **Review-file prompt-injection defense: anchor text HTML-escaped and code-fenced.**
15. **Backward-compat: batch-1 lead missing 4 optional fields passes every reader + schema.**

## Acceptance Criteria

### Functional Requirements

- [x] `discover-jobs` writes new leads to `data/leads/` with `discovered_via` populated.
- [x] All 7 dedup buckets appear in the run artifact JSON (empty arrays when no members).
- [x] Filters evaluate with documented precedence; substring + case-insensitive; `keywords_none` wins.
- [x] `--dry-run` is a true no-op on disk.
- [x] `--max-ingest N` caps leads written AND leaves cursor unchanged for budget-capped sources.
- [x] `--no-score` leaves leads at `status: discovered` with no `fit_assessment`.
- [x] `--sources greenhouse,lever,careers` accepted; unknown tokens rejected.
- [x] `--score-concurrency N` parallelizes batched scoring.
- [x] `--reset-cursor "ExampleCo|greenhouse"` and `--reset-cursor "ExampleCo|*"` both supported (v3: `|` separator).
- [x] `--watchlist PATH` loads an alternate config.
- [x] `discovery-state` emits JSON listing all `(company, source, last_run_at, last_run_status, last_entry_count)` tuples; `--last-run` and `--bucket` filter the latest run artifact (v3).
- [x] `watchlist-show` / `watchlist-add` / `watchlist-remove` / `watchlist-validate` manipulate `config/watchlist.yaml` atomically with safe input escaping (v3).
- [x] `watchlist-add` raises `watchlist_comments_present` when target file has comments unless `--force` supplied (v3).
- [x] `review-list` / `review-promote` / `review-dismiss` operate on `data/discovery/review/<entry_id>.md` by ID (v3: single-file design).
- [x] `review-promote` validates `entry_id` against `ENTRY_ID_RE` before filesystem access; calls `ingest_url` (which re-validates `candidate_url` through `_validate_url_for_fetch`); writes `discovered_via: careers_html_review` on the new lead; updates review frontmatter `status: promoted`.
- [x] `robots-cache-clear` flushes `data/discovery/robots_cache.json` (v3).
- [x] Greenhouse and Lever listing fetchers return `ListingEntry.posting_url` values accepted by existing `ingest_url`.
- [x] Career crawler tries JSON-LD → ATS subdomain → heuristic, in that order.
- [x] Career crawler requires ≥2 signals for auto-queue; 1-signal lands in `data/discovery/review/`.
- [x] LinkedIn and Indeed hard-fail at every entry point.
- [x] HTTPS-only enforced on `careers_url` (schema pattern `^https://`).
- [x] Anti-bot challenge detected via status + header (NOT body-alone) (v3); host marked `anti_bot_blocked`, not retried mid-run.
- [x] Cursor persists at `data/discovery/state.json` validated against `discovery-cursor.schema.json` with `schema_version: 1` and `|` key separator (v3).
- [x] Cursor advances ONLY for complete, non-truncated, non-budget-capped sources; `last_run_status` only writes `complete` or `failed` (v3: `partial` removed).
- [x] Run artifact persists at `data/discovery/history/<timestamp>.json` validated against concretely-specified `discovery-run.schema.json` (v3).
- [x] Review entries persist as **single `.md` with YAML frontmatter** (v3, collapsed from paired `.md`+`.json`); frontmatter validates against `discovery-review.schema.json`; body uses nonce-fenced block for attacker-controlled content.
- [x] Robots cache persists at `data/discovery/robots_cache.json` with differentiated TTLs (allow 24h, disallow 1h) and resolved-IP invalidation (v3).
- [x] Watchlist config validates against `watchlist.schema.json`; malformed raises `watchlist_invalid`.
- [x] Watchlist `name` regex `^[A-Za-z0-9 ._-]{1,64}$` enforced; `careers_url` must be HTTPS; `maxItems: 200` cap enforced.
- [x] Review `entry_id` regex `^[a-f0-9]{16}$` enforced via schema.
- [x] `discovered_via` appended under per-lead-id lock (WeakValueDictionary — todo #040) on `already_known` and `duplicate_within_run` paths (provenance never lost). Missing lead file raises `lead_write_race`.
- [x] Malformed `discovered_via` on existing lead (non-list value) → warning logged + reset to `[]` + append proceeds (v3, todo #030).
- [x] Scoring phase scans `data/leads/*.json` for `status: discovered` leads missing `fit_assessment` and rescores them (v3, todo #033 — crash-recovery).

### Non-Functional Requirements

- [x] No new default dependencies in `pyproject.toml`.
- [x] Per-domain minimum interval: 500ms, globally enforced, reserve-first (no thundering herd).
- [x] Max concurrent HTTP requests to same registered domain: 1 (test-enforced invariant).
- [x] Max parallel company workers: 3 (`ThreadPoolExecutor`).
- [x] Max parallel scoring workers: configurable via `--score-concurrency`, default 3.
- [x] All network tests use fixtures via `--html-file` or patched `fetch`; no real HTTP.
- [x] Listing fetch size cap: 8MB ingress, 20MB decompressed (proportional 2.5× ratio).
- [x] Per-posting fetch size cap unchanged from batch 2 (2MB / 5MB).
- [x] **DNS-rebinding TOCTOU closed via `_PinnedHTTPSConnection`** with:
  - `server_hostname=hostname` for SNI
  - `check_hostname=True`, `verify_mode=CERT_REQUIRED`
  - `Connection: close` to defeat pool reuse
  - Per-redirect re-pin via `_StrictRedirectHandler`
- [x] IPv4-mapped-IPv6 addresses blocked (`::ffff:127.0.0.1` → `private_ip_blocked`).
- [x] `registered_domain()` handles IP URLs, IDN/Punycode, empty hostnames.
- [x] `urllib.robotparser` wrapped for spec-correct 5xx and BOM handling.
- [x] Robots body capped at 500KB.
- [x] Robots cache: 1h TTL for disallow decisions, 24h for allow; IP-invalidation on re-resolve.
- [x] `FETCH_CHAIN_TIMEOUT_S = 20` outer timeout across redirects.
- [x] `write_json` per-call unique tmp + parent-directory fsync (v3).
- [x] `_LEAD_WRITE_LOCKS` uses `WeakValueDictionary` (v3) — no memory leak.
- [x] Startup sweep globs `*.tmp` (catches batch-2 and batch-3 mkstemp patterns).
- [x] Every new CLI command emits JSON by default.
- [x] CLI uniform `StructuredError` handler (v3).

### Quality Gates

- [x] Every new CLI command has tests.
- [x] All 156 batch-2 tests continue to pass unchanged.
- [x] New tests ≥60 across Phases 1-5 (up from v2's ≥45 due to v3 additions).
- [x] `check_integrity` detects all 5 new orphan types (validated by fixtures).
- [x] `DISCOVERY_ERROR_CODES` frozen set matches every `raise DiscoveryError(...)` call (enforced by grep test).
- [x] `SOURCE_NAME_MAP` consistency test: CLI tokens, `ListingEntry.source` literals, `discovered_via.source` enum all stay aligned.
- [x] `DISCOVERY_USER_AGENT` constant is the single source of truth (grep test: no string-literal `"job-hunt/"` in discovery.py / net_policy.py / utils.py outside the constant definition).
- [x] `StructuredError` is the common base for all three structured-error subclasses; CLI handler catches it uniformly.
- [x] `_PinnedHTTPSConnection` used for every HTTPS fetch; TLS integrity preserved (test-enforced: cert validation under pin, redirect re-pin, no pool reuse).
- [x] `simple_yaml` write-path escapes all user inputs; watchlist-add rejects control characters; comment-loss requires `--force`.
- [x] `AGENTS.md` Discovery Guardrails section + schema versioning convention subsection.
- [x] `README.md` Quick Start includes discovery step.
- [x] `docs/guides/job-discovery.md` exists and walks a user through first-run.
- [x] Backward-compat test round-trips a batch-1 lead through 6 readers (validate, score, ats-check, apps-dashboard, analyze-skills-gap, check-integrity).

## Success Metrics

- User adds 5 target companies to `config/watchlist.yaml` and `discover-jobs` surfaces ≥10 new openings on first run.
- Second-run false-positive rate ≤20%.
- Daily discovery loop + review triage takes <2 minutes of user attention once stable.
- Generic career crawler produces ≥1 high-confidence lead per 5 company-site crawls (excluding anti-bot-blocked hosts).
- 50-company watchlist completes in 3-6 minutes steady-state (persistent robots cache warm); 8-12 minutes cold start.
- Review file accumulation <10 unreviewed entries at any time.

## Dependencies & Prerequisites

- Phase 1 depends on batch 1 and batch 2 being present; introduces one batch-2 security patch (DNS-rebinding).
- Phase 2 depends on Phase 1.
- Phase 3 depends on Phase 1 (robots cache).
- Phase 4 depends on Phases 1-3.
- Phase 5 depends on Phase 4.

No external infra, no API keys, no deploy surface.

## Risk Analysis & Mitigation

### Risk: Greenhouse/Lever listing APIs change format

**Mitigation:** both parsers are small and well-tested. Changes localized to one function each. 500ms per-domain interval unlikely to trigger rate limits (documented public APIs, generous budgets).

### Risk: Generic career crawler garbage leads

**Mitigation:** JSON-LD-first (structured data, canonical). ATS-subdomain detection (high precision). Heuristic fallback requires ≥2 signals. Low-confidence channel into `data/discovery/review/`. LinkedIn/Indeed hard-fail. `anti_bot_blocked` error for Cloudflare/Akamai. HTTPS-only on `careers_url`.

### Risk: JS-rendered SPA career pages

**Mitigation:** documented limitation in `job-discovery.md`. User adds Greenhouse/Lever slug if available. Generic crawler produces `anti_bot_blocked` for common SPA-gate patterns.

### Risk: Cursor corruption

**Mitigation:** atomic `write_json` + schema validation. On parse/schema failure → `DiscoveryError(cursor_corrupt)` with remediation text: `rm data/discovery/state.json`. Orphaned `.tmp` files swept on startup.

### Risk: robots.txt returns HTML or other non-parseable body

**Mitigation:** wrapped parser is tolerant. BOM stripped. 5xx treated as "disallow, retry in 24h" (spec-correct). 500KB body cap.

### Risk: User accidentally commits `config/watchlist.yaml` with target-company names

**Mitigation:** `.gitignore` extended in Phase 1. Tracked `config/watchlist.example.yaml` as template. Convention documented in `AGENTS.md` so future sensitive configs follow suit.

### Risk: 50+ company watchlist → slow run

**Mitigation:** persistent robots cache + coalesced cursor writes + batched scoring → 3-6 min steady-state. Documented. If this becomes a real bottleneck, per-source parallelism within a company is a one-line change.

### Risk: Filter semantics confuse the user

**Mitigation:** `docs/guides/job-discovery.md` includes 3 worked filter examples with expected outputs. Test names enumerate precedence. Watchlist example YAML has inline comments.

### Risk: DNS-rebinding attacker abuses discovery fan-out

**Mitigation:** Phase 1 IP-pinning closes the TOCTOU window. Robots fetch, listing fetch, detail fetch all go through the same hardened path. Regression test explicitly proves pin holds under attacker flip.

### Risk: Review file prompt injection of agents consuming the file

**Mitigation:** HTML-escape + code-fence + "DATA NOT INSTRUCTIONS" banner + paired schema-validated JSON companion. Agents should prefer the `.json` file; `.md` is for humans.

### Risk: LLM scoring dominates runtime

**Mitigation:** batched post-ingestion via separate ThreadPool. `--no-score` opt-out. `--score-concurrency N` tunable.

### Risk: simple_yaml extension breaks existing configs

**Mitigation:** Phase 1 regression test loads every existing `config/*.yaml` file and compares parsed output before/after.

## Documentation Plan

- [x] `docs/guides/job-discovery.md` — watchlist setup, filter semantics with 3 worked examples, cursor behavior, review triage workflow, LinkedIn/Indeed policy, troubleshooting, config-tracking convention deviation.
- [x] `prompts/discovery/career-crawl.md` — agent guidance for review entries.
- [x] `README.md` Quick Start — discovery step.
- [x] `AGENTS.md` — Discovery Guardrails + DISCOVERY_ERROR_CODES + gitignore convention.
- [x] `docs/profile/README.md` — cross-link.
- [x] Inline module docstrings matching `ingestion.py` style.

## Recommended Sequence Of Work

1. Phase 1 (infrastructure + batch-2 SSRF patch + utils upgrades + simple_yaml extension).
2. Phase 2 (board listing fetchers).
3. Phase 3 (generic crawler with JSON-LD + ATS + heuristic ladder).
4. Phase 4 (orchestration + full CLI surface including review/watchlist/state commands).
5. Phase 5 (docs + integration + check-integrity extension + backward-compat hardening).
6. Run `discover-jobs` against a 3-company watchlist to validate end-to-end.
7. User populates `config/watchlist.yaml` with real targets.

## Sources & References

### Origin

- **Brainstorm:** [docs/brainstorms/2026-04-15-job-hunt-brainstorm.md](../brainstorms/2026-04-15-job-hunt-brainstorm.md). Key decisions carried forward:
  - "Job Discovery: support two families of sources" — Greenhouse/Lever (boards) + generic crawler (company sites).
  - "Normalized lead format" — everything routes through `ingest_url` → `extract_lead`.
  - "Do not fabricate" — `weak_inference` confidence on single-source crawls + ≥2-signal gate.
  - "Phased rollout — Phase 1: Trustworthy Discovery" — this IS Phase 1.
  - "LinkedIn and Indeed hard-fail" — preserved from batch 2.

### Internal References

- Batch 2 plan: [docs/plans/2026-04-16-003-feat-pdf-url-ats-analytics-plan.md](2026-04-16-003-feat-pdf-url-ats-analytics-plan.md)
- Batch 1 plan: [docs/plans/2026-04-15-002-feat-content-generation-and-tracking-plan.md](2026-04-15-002-feat-content-generation-and-tracking-plan.md)
- Batch 0 plan: [docs/plans/2026-04-15-001-feat-agent-first-job-hunt-system-plan.md](2026-04-15-001-feat-agent-first-job-hunt-system-plan.md)
- `src/job_hunt/ingestion.py` — `fetch` (promoted), `_validate_url_for_fetch`, `_StrictRedirectHandler`, `canonicalize_url`, `GREENHOUSE_URL_RE`, `LEVER_URL_RE`, `HARD_FAIL_URL_PATTERNS`, `IngestionError`.
- `src/job_hunt/utils.py` — `write_json` (upgraded), new `DomainRateLimiter`, new `RobotsCache`.
- `src/job_hunt/core.py` — `score_lead` line 1154, `extract_lead` line 1027.
- `src/job_hunt/simple_yaml.py` — extended in Phase 1.
- `schemas/lead.schema.json`.
- Batch-2 intake lifecycle: `ingestion.py::ingest_url` pending/processed/failed pattern.
- **Learnings applied:**
  - [reconcile-plan-after-multi-agent-deepening-review](../solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md) — this plan's Enhancement Summary, code blocks, schemas, deliverables, and acceptance criteria are synchronized (no split-brain).
  - [review-deepened-plans-before-implementation](../solutions/workflow-issues/review-deepened-plans-before-implementation.md) — this deepening pass ran the security/data-integrity/simplicity/pattern/agent-native/split-brain reviews BEFORE implementation.
  - [extend-cli-with-new-modules-without-breaking-backward-compat](../solutions/workflow-issues/extend-cli-with-new-modules-without-breaking-backward-compat.md) — shared utilities in `utils.py`; lazy CLI imports; optional schema fields with `.get()`; paired-write corruption avoided.
  - [harden-profile-normalization-signal-selection](../solutions/workflow-issues/harden-profile-normalization-signal-selection.md) — ≥2-signal gate and trust-tiered extraction mirror the profile-ingestion hardening.
  - [design-secret-handling-as-a-runtime-boundary](../solutions/security-issues/design-secret-handling-as-a-runtime-boundary.md) — `config/watchlist.yaml` in `.gitignore`; redacted-URL logging already in batch 2.

### External References (research collected during deepening)

- **Greenhouse Job Board API (listings):** [developers.greenhouse.io/job-board.html](https://developers.greenhouse.io/job-board.html). `GET /v1/boards/{company}/jobs`, unauth, returns all current openings. `?content=true` returns double-HTML-encoded content.
- **Lever Postings API:** [github.com/lever/postings-api](https://github.com/lever/postings-api). `GET /v0/postings/{company}?mode=json`, unauth, `createdAt` is ms epoch.
- **RFC 9309 Robots Exclusion Protocol:** [rfc-editor.org/rfc/rfc9309](https://www.rfc-editor.org/rfc/rfc9309.html). Governs 5xx handling, group matching.
- **Google robots.txt spec:** [developers.google.com/search/docs/crawling-indexing/robots/robots_txt](https://developers.google.com/search/docs/crawling-indexing/robots/robots_txt).
- **`urllib.robotparser` limitations:** [cpython#138907](https://github.com/python/cpython/issues/138907) — not RFC 9309 compliant; batch 3 wraps with spec-correct behavior.
- **schema.org JobPosting:** [schema.org/JobPosting](https://schema.org/JobPosting).
- **Google For Jobs structured data:** [developers.google.com/search/docs/appearance/structured-data/job-posting](https://developers.google.com/search/docs/appearance/structured-data/job-posting).
- **Cloudflare bot detection engines:** [developers.cloudflare.com/bots/concepts/bot-detection-engines](https://developers.cloudflare.com/bots/concepts/bot-detection-engines/).
- **Python 3.12 stdlib:** `urllib.robotparser`, `concurrent.futures.ThreadPoolExecutor`, `threading`, `time.monotonic`, `os.replace`, `tempfile.mkstemp`, `socket.getaddrinfo`, `json.loads`.
- **ATS platform references (batch 4 candidates):**
  - [Ashby Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)
  - [SmartRecruiters Postings API](https://developers.smartrecruiters.com/docs/get-job-postings)
  - [fantastic.jobs: ATS with public APIs](https://fantastic.jobs/article/ats-with-api)
  - [plibither8/jobber](https://github.com/plibither8/jobber) — reference implementation

### Related Work

- Profile ingestion audit (2026-04-15) — 94% profile completeness; discovery consumes that profile via batched `score_lead`.
- SpecFlow analysis of the v1 plan (2026-04-16) — 10 gaps identified, all folded into Phase 1-5 deliverables.
- Deepening review of v1 plan (this pass, 2026-04-16) — 12 parallel agent reviews across security, data integrity, architecture, performance, Python style, simplicity, pattern consistency, agent-native completeness, split-brain prevention, best-practices research, framework-docs research, and three learning cross-checks. Output synthesized into this v2 plan.
