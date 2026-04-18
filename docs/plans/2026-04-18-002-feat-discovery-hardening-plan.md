---
title: "feat: Discovery hardening — anti-bot pacing, Indeed parser refresh, keyword hygiene, ATS tier fix, curated resume lanes"
type: feat
status: active
date: 2026-04-18
origin: accumulated uncommitted changes in working tree on 2026-04-18 after feat/cover-letter-lanes PR
notes: "Splits ~553 lines of WIP across 8 src files + 2 docs into 10 reviewable phases. Does NOT touch candidate_name gap or Phase 5 of the cover-letter-lanes plan."
---

# feat: Discovery Hardening

## Enhancement Summary

**Deepened on:** 2026-04-18 via six parallel research/review agents (spec-flow, security, performance, architecture, simplicity, framework-docs).

**Key improvements folded in:**
1. Phase count 10 → 8 (merged jitter primitive + wiring; folded docs into Phase 8 tail).
2. Phase 0 test approach rewritten to grep across the repo, not a single module, so Phase 2's constant relocation doesn't break it again.
3. `utils.repo_root()` replaces the invented `repo_root` kwarg in Phase 8 (existing repo convention).
4. Added `RobotsCache` UA thread-through to Phase 2's acceptance criteria.
5. Added Phase 7 one-shot migration sub-step for application records previously demoted by advisory `warnings`.
6. Added JSON-LD DoS-safety requirements to Phase 5 (size cap, canonical regex shape, broader exception handling).
7. Added `set_human_jitter` upper-bound guard (reject `max_s > 30.0`) and restricted its intended scope.
8. Added `lead.schema.json` + `schemas/generated-content.schema.json` (`curated` provenance) to Files Likely To Change.
9. Swapped the Phase 4 mutate-after-write pattern for `override_hints` threaded into `ingest_url` before the first `write_json`.
10. Added wall-clock expectation (~4-5 min per `discover-jobs` run) + progress logging recommendation.

**New code fragments carried from research:**
- `email.utils.parsedate_to_datetime` pattern for `Retry-After` parsing (RFC 9110 compliant).
- Canonical `application/ld+json` extraction regex + `html.unescape` + `@graph` walker.
- `random.uniform` is appropriate for pacing jitter (non-cryptographic); `secrets` is explicitly not needed.

## Overview

Several days of iterative work on discovery, ingestion, and ATS tooling accumulated as uncommitted changes in the working tree while the `feat/cover-letter-lanes` PR was shipped. This plan splits that WIP into reviewable phases so each change lands with targeted test coverage, a coherent commit message, and the tolerant-consumer ordering the repo already uses.

Scope covers:

- Anti-bot signatures (User-Agent + human-jitter rate limiting on `indeed.com` / `linkedin.com`)
- Indeed 2026 DOM refresh (new card shape, `employer_name` field, pagination tighten)
- HTTP 429 handling surfaces `Retry-After` to the caller
- Direct `_fetch_indeed_viewjob` that pulls JobPosting JSON-LD instead of shoveling the whole React chrome into the description
- Keyword hygiene in `extract_lead` (stopword filter) plus matching ATS density threshold (5% → 10%)
- ATS tier calculation: "warnings" is advisory, not a tier-2 demotion; `_run_ats_check` now reads the report from the record's `report_path` (was reading the record itself as the report — load-bearing bug)
- Curated resume lanes: pre-written ATS-passing resumes are selected by lead title keyword, bypassing the thin template for the lead shapes the candidate actually applies to
- Profile-report refreshes that should ride along

Not in scope:

- `candidate_name` normalization gap (deferred in the cover-letter-lanes plan; separate PR)
- Cover-letter lanes Phase 5 (normalized fragment layer, explicit later slice)

## Problem Statement

1. **CI blocked**: `test_discovery_user_agent_constant_single_sourced` fails against the working tree because the user-agent constant no longer contains the substring `"job-hunt/"`. The test was grep-based; it needs to track the new constant by name.
2. **Bot fingerprint**: The current `job-hunt-cli/0.2` User-Agent is an obvious automation signal. Clockwork rate limiting is itself a fingerprint even with a realistic UA.
3. **Indeed parser rot**: Indeed's 2026 card shape (`<span id="jobTitle-{jk}">`, `aria-label="full details of …"`) doesn't match the legacy regex. Card data is lost; `ingest_url` fallback scrapes `<title>` and host → leads land with `company="www"` and `title="…- Indeed.com"`.
4. **Indeed viewjob pollution**: Without a dedicated ingestion path, the whole React page (≈750 KB) becomes the `description`, poisoning skills/keyword extraction downstream.
5. **Keyword pollution**: `extract_lead.keywords` is just the top-20 most frequent tokens, which is dominated by stopwords (`the`, `and`, `with`, `you`, `role`, `team`, etc.). This inflates the ATS stuffing metric because those tokens also appear heavily in any real resume.
6. **ATS stuffing false positives**: The 5% density threshold catches every realistic tailored resume; production-grade copy naturally lands in the 5-9% range once stopwords are out.
7. **ATS tier demotion bug**: `_compute_tier` was demoting to `tier_2` whenever `ats_status == "warnings"`, so every resume with a `keyword_coverage_below_target` warning — which is advisory only — got flagged for manual review. Also, `_run_ats_check` was reading `errors`/`warnings` off the content record, but `run_ats_check_with_recovery` returns the **record**, not the report, so those lists were always empty.
8. **Weak template output**: The thin template produces ~190-word generic resumes that fail length + density gates. Candidate already has three hand-crafted, ATS-passing resumes (`data/generated/resumes/kashane-sakhakorn-*.md`) that should be used verbatim for matching lead shapes.

## Goals

- Unblock CI by fixing the single-sourcing test against the new constant.
- Land each concern as its own reviewable phase with tests, following the `feat(<batch>): Phase N` convention already used for `batch4` and `cover-letter-lanes`.
- Ship the tolerant-consumer side before the strict-producer side where applicable (ATS tier change ships before it is relied on by new warning semantics).
- Keep every change local + deterministic; no network calls in tests; no destructive operations.

## Non-Goals

- Not touching `preferences.candidate_name` in the normalized profile (separate PR tracked by the cover-letter-lanes plan).
- Not implementing Phase 5 of the cover-letter-lanes plan (normalized fragment layer).
- Not adding an HTML-to-Markdown or headless-browser layer; we stay on stdlib `urllib` + regex.
- Not building a full ToS-compliance auditing layer. Human-in-the-loop on submit remains the ToS defense per `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`; this plan only reduces the signature of the discovery/ingestion layer.

## Current Repeatable Pipeline

Still intact; this plan refines specific stages:

1. `discover-jobs` → listing cards → `ListingEntry` → `write_json` to `data/leads/*.json`.
2. `ingest-url` → `fetch` → `_dispatch_url` → parsed lead JSON.
3. `extract_lead` derives `normalized_requirements.keywords` from title + body.
4. `generate-resume-variants` renders markdown + content records.
5. `run_ats_check_with_recovery` patches content record with ATS status.
6. `application.py` computes tier + generates application artifacts.

## Relevant Learnings To Carry Forward

- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md` — Humans remain the submit actor; discovery/ingestion may reduce automation signatures but do not bypass the compliance boundary.
- `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md` — Phase 7 (ATS tier) ships before any code depends on the new warning-is-advisory semantics.
- `docs/solutions/workflow-issues/integrate-review-findings-into-deepened-plan-without-split-brain.md` — When a constant has an assertion-invariant test around it (like `DISCOVERY_USER_AGENT`), update prose, code, test, and acceptance criteria in a single atomic pass.

## Grounding Contract

Each phase has an independent commit + test surface. A later phase never assumes a field, constant, or helper added in an earlier phase is already reviewed — cross-phase references are either explicit imports or documented in the phase body. No "giant dirty diff" merges.

## Phase Constants (consolidated)

Numeric / format decisions referenced inline in later phases. Kept minimal; the numbers that matter appear next to the code that uses them.

- `DISCOVERY_USER_AGENT` — Chrome 131 Mac string, defined once at `net_policy.py` module scope and imported by `discovery.py` + `ingestion.py`.
- `set_human_jitter("indeed.com", 3.0, 7.0)` and `set_human_jitter("linkedin.com", 3.0, 7.0)` — inline in `discover_jobs`; no tuple indirection.
- Indeed: `MAX_PAGES_PER_RUN=2`, `result_cap=20` default.
- `KEYWORD_DENSITY_STUFFING_THRESHOLD=0.10`.
- `set_human_jitter` accepts `min_s > 0`, `max_s >= min_s`, and `max_s <= 30.0` (upper bound guard against accidental budget starvation).

## Implementation Phases

### Phase 0: Retarget the UA single-sourcing test (prerequisite) ✅

- [ ] In `tests/test_discovery.py:421` replace the `"job-hunt/"` substring grep with a cross-repo grep that imports `DISCOVERY_USER_AGENT` from whichever module owns it and asserts the literal UA string appears **exactly once** across `src/job_hunt/`:

  ```python
  # tests/test_discovery.py
  def test_discovery_user_agent_constant_single_sourced(self) -> None:
      from job_hunt.discovery import DISCOVERY_USER_AGENT
      src_root = ROOT / "src" / "job_hunt"
      literal_hits: list[str] = []
      for py_file in src_root.rglob("*.py"):
          for ln in py_file.read_text(encoding="utf-8").splitlines():
              if DISCOVERY_USER_AGENT in ln:
                  literal_hits.append(f"{py_file.name}: {ln.strip()}")
      self.assertEqual(len(literal_hits), 1, literal_hits)
  ```

- [ ] Commit: `test(discovery-hardening): Phase 0 — retarget UA single-sourcing test across src/`

### Phase 1: net_policy jitter primitive + wire it up ✅

Merged from a prior 2-phase split — the primitive is useless without callers, and the combined commit is still under ~80 lines.

- [ ] Add `_DomainBudget.max_interval_s: float = 0.0` and `pick_interval()` returning `random.uniform(min, max)` when `max > min`, else `min`. Mersenne-Twister `random` is appropriate here — the use case is non-cryptographic pacing jitter, not unpredictability against an adversary (Python docs `secrets` intro explicitly scopes `secrets` to auth/tokens).
- [ ] Add `DomainRateLimiter.set_human_jitter(domain, min_s, max_s)` with validation:
  - `min_s > 0`, `max_s > 0` → `ValueError`
  - `max_s < min_s` → `ValueError`
  - `max_s > 30.0` → `ValueError` (guards against misuse on budget-sensitive hosts)
- [ ] `set_domain_interval(domain, seconds)` must reset `max_interval_s = 0.0` so switching back to deterministic pacing works.
- [ ] Docstring for `set_human_jitter` explicitly scopes the primitive to defensive read-path pacing for the user's own job search against public listing pages (not a write-path throttle).
- [ ] In `discover_jobs(...)`, after constructing `rate_limiter`, call `set_human_jitter("indeed.com", 3.0, 7.0)` and `set_human_jitter("linkedin.com", 3.0, 7.0)` inline. No `HUMAN_JITTER_HOSTS` tuple.
- [ ] Tests in `tests/test_net_policy.py` and `tests/test_discover_jobs.py`:
  - `test_set_human_jitter_validates_range` — negative / zero / inverted / > 30 → `ValueError`
  - `test_human_jitter_samples_inside_range` — 100 samples of `pick_interval()` all in `[min, max]`
  - `test_set_domain_interval_resets_jitter` — set jitter, then `set_domain_interval`; next `pick_interval` returns min only
  - `test_discover_jobs_installs_human_jitter_for_known_hosts` — patch `DomainRateLimiter.set_human_jitter`; assert called twice with the exact `(host, min, max)` triples
- [ ] Commit: `feat(discovery-hardening): Phase 1 — human-jitter rate limiting for indeed + linkedin`

### Phase 2: Anti-bot HTTP surface ✅

- [ ] Define `DISCOVERY_USER_AGENT: Final` in `src/job_hunt/net_policy.py` at module scope, clearly annotated "default HTTP identity; `RobotsCache` and other UA-agnostic helpers still accept a UA arg." Ingestion + discovery both import from there.
- [ ] `src/job_hunt/ingestion.py:fetch`: use the shared `DISCOVERY_USER_AGENT`; add browser-shape `Accept` (`text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8`) and `Accept-Language: en-US,en;q=0.9`.
- [ ] `src/job_hunt/ingestion.py:fetch` HTTP 429 path: raise `IngestionError(error_code="rate_limited", url=url)` with a message that includes parsed `Retry-After` when present. Use `email.utils.parsedate_to_datetime` for HTTP-date parsing (RFC 9110 §10.2.3):

  ```python
  from email.utils import parsedate_to_datetime
  from datetime import datetime, timezone

  def _parse_retry_after(value: str) -> float | None:
      value = value.strip()
      if value.isdigit():
          return float(value)
      try:
          dt = parsedate_to_datetime(value)
          if dt.tzinfo is None:
              dt = dt.replace(tzinfo=timezone.utc)
          return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
      except (TypeError, ValueError):
          return None
  ```

- [ ] **Caller audit**: enumerate every `ingestion.fetch` call site (`discovery._run_source`, `RobotsCache._fetch_robots`, and any other place) and confirm that a raised `rate_limited` `IngestionError` is either caught or intentionally propagated. The previous silent fall-through was a bug; this audit keeps the run from aborting on a single 429.
- [ ] **RobotsCache UA thread-through**: `discover_jobs` constructs `RobotsCache(...)` with the new `DISCOVERY_USER_AGENT`. Robots fetches for indeed/linkedin must not leak the old `job-hunt-cli/0.2` identity — acceptance test: `RobotsCache` instance returned by `discover_jobs` has `_user_agent == DISCOVERY_USER_AGENT`.
- [ ] Tests in `tests/test_ingestion.py`:
  - `test_fetch_sends_chrome_user_agent` (inspects `req.headers` via a `urllib` mock)
  - `test_fetch_429_raises_with_retry_after_seconds`
  - `test_fetch_429_raises_with_retry_after_http_date`
  - `test_parse_retry_after_handles_both_forms`
- [ ] Commit: `feat(discovery-hardening): Phase 2 — chrome UA + RFC 9110 Retry-After handling`

### Phase 3: Indeed parser hardening ✅

- [x] `src/job_hunt/indeed_discovery.py` — `_TITLE_RE` three-alternation: `<span id="jobTitle-{jk}">…</span>`, `aria-label="full details of <title>"` on the jcs-JobTitle link, and the legacy `<h2 class="jobTitle">` shape.
- [x] `MAX_PAGES_PER_RUN`: 10 → 2. `discover_indeed_search(result_cap=20)` default.
- [x] `employer_name: str = ""` on `IndeedJobPosting` and `ListingEntry` (+ reflected in `to_dict`).
- [x] `schemas/lead.schema.json` uses open `additionalProperties` — existing leads remain valid; no schema change required (documented in PR).
- [ ] **Follow-up**: replace the mutate-after-write lead patch with an `override_hints` kwarg threaded into `ingest_url`. Deferred to a dedicated PR because the refactor touches every `ingest_url` call site. Current shape (patch-after-write) is guarded to only overwrite obviously-broken fields and is covered by `write_json`'s atomic temp-rename, so it ships safely for v1.
- [ ] **International Indeed**: document that `*.indeed.co.uk` buckets under `co.uk` (eTLD+1) and therefore bypasses the `indeed.com` jitter policy. v1 scope stays on `indeed.com`; a follow-up slice can add per-suffix coverage if the candidate's lead sources include international Indeed. Flag in Open Questions.
- [ ] Tests in `tests/test_indeed_discovery.py`:
  - Fixtures for all three markup shapes; assert all parse to a populated `IndeedJobPosting` with `employer_name` set
  - `test_discover_indeed_search_default_cap_respects_new_defaults` — default `result_cap=20` stops pagination at the second page
  - `test_ingest_url_merges_override_hints` — pass hints; assert the written lead reflects them without requiring a second write
- [ ] Commit: `feat(discovery-hardening): Phase 3 — Indeed 2026 card shape + override-hints lead plumbing`

### Phase 4: Indeed viewjob ingestion

- [ ] Add `_fetch_indeed_viewjob(url, html_text=None)` in `src/job_hunt/ingestion.py`. Uses a canonical stdlib-only JSON-LD extractor:

  ```python
  import html, json, re

  _LD_RE = re.compile(
      r'<script\b[^>]*?\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
      re.DOTALL | re.IGNORECASE,
  )
  _MAX_LD_BLOCK_BYTES = 512_000  # reject anything larger as hostile input

  def _iter_jobpostings(html_text: str):
      for m in _LD_RE.finditer(html_text):
          raw = m.group(1).strip()
          if len(raw) > _MAX_LD_BLOCK_BYTES:
              continue
          raw = raw.replace(r"<\/", "</")  # tolerate escaped </script>
          try:
              data = json.loads(raw)
          except (json.JSONDecodeError, ValueError, RecursionError):
              continue
          for node in _walk(data):
              t = node.get("@type")
              if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                  if isinstance(node.get("description"), str):
                      node["description"] = html.unescape(node["description"])
                  yield node

  def _walk(obj):
      if isinstance(obj, dict):
          if "@graph" in obj and isinstance(obj["@graph"], list):
              for x in obj["@graph"]:
                  yield from _walk(x)
          else:
              yield obj
              for v in obj.values():
                  yield from _walk(v)
      elif isinstance(obj, list):
          for x in obj:
              yield from _walk(x)
  ```

- [ ] Fallback path when JSON-LD is absent or every block is malformed: extract the `#jobDescriptionText` innerHTML as description and reuse Phase 3 regexes for title/company.
- [ ] Wire `ingest_url` to dispatch `indeed.com/viewjob` URLs to `_fetch_indeed_viewjob` before the generic HTML path.
- [ ] **Security**: extracted `description`/`title`/`company` are treated as untrusted text. Do not re-emit them into HTML/markdown surfaces without escaping. LLM consumers receive them as plain text.
- [ ] Tests in `tests/test_ingestion.py`:
  - `test_fetch_indeed_viewjob_parses_json_ld` (fixture)
  - `test_fetch_indeed_viewjob_walks_graph_wrapper` (Yoast-style `@graph` array)
  - `test_fetch_indeed_viewjob_falls_back_to_jobdescription` (malformed JSON-LD)
  - `test_fetch_indeed_viewjob_rejects_oversized_block` (>512 KB block is skipped, not parsed)
  - `test_fetch_indeed_viewjob_handles_type_list` (`@type: ["JobPosting", "Thing"]`)
  - `test_ingest_url_dispatches_viewjob_path` (stubbed HTTP; assert `_fetch_indeed_viewjob` invoked)
- [ ] Commit: `feat(discovery-hardening): Phase 4 — Indeed viewjob JSON-LD ingestion`

### Phase 5: Keyword hygiene

Coupled phase: the ATS density threshold only makes sense once stopwords are gone from `keywords`.

- [ ] `src/job_hunt/core.py:extract_lead`:
  - Add `_KEYWORD_STOPWORDS: Final[frozenset[str]]` at **module scope** (policy constant, not inside the function).
  - Require `len(word) >= 3` and `word not in _KEYWORD_STOPWORDS` before including.
  - Take from `Counter.most_common(100)` then filter (heap cost is sub-millisecond even on 10k-token bodies — `heapq.nlargest` is O(n log 100)).
- [ ] `src/job_hunt/ats_check.py`: `KEYWORD_DENSITY_STUFFING_THRESHOLD: Final = 0.10` with the updated comment block.
- [ ] Tests:
  - `tests/test_ingestion.py` or `tests/test_discovery.py`: `test_extract_lead_filters_stopwords`
  - `tests/test_ats_check.py`: `test_realistic_density_not_flagged_as_stuffing` (7% → no error) and `test_density_above_ten_percent_still_flagged` (12% → error)
- [ ] Commit: `feat(discovery-hardening): Phase 5 — keyword stopword filter + density realignment`

### Phase 6: Application tier + ATS recovery bug fix + back-fill

- [ ] `src/job_hunt/application.py:_compute_tier`: delete the `if ats_status == "warnings": return "tier_2"` branch. Add a comment block explaining warnings are advisory per the original ATS design contract.
- [ ] `src/job_hunt/application.py:_run_ats_check`: `run_ats_check_with_recovery` returns the content record, not the report. Rewrite to load the report from `ats_meta["report_path"]`, lift errors/warnings from it, and append `ats_meta["error"]` when status is `check_failed`. Also: if `report_path` is set but the file is missing, surface that as a synthetic `errors` entry so `_compute_tier` does not over-promote a broken report to tier_1.
- [ ] **One-shot migration**: add a small idempotent back-fill that scans existing `data/applications/*-status.json` records and re-stamps any record whose **only** tier-2 rationale was `ats_status:warnings`. Records with additional tier-2 reasons (unresolved fields, errors) stay tier_2. Emit a `tier_recomputed_at` timestamp on each touched record. Run it as a one-shot CLI subcommand (e.g., `check-integrity --recompute-tiers` or a dedicated `recompute-tiers` subcommand) rather than auto-running on import.
- [ ] Tests in `tests/test_phase4_application.py`:
  - `TierComputationTest`: warnings → tier_1; errors/check_failed/not_checked → tier_2
  - `test_run_ats_check_loads_report_from_record_path`
  - `test_run_ats_check_surfaces_missing_report_as_error`
  - `test_recompute_tiers_migrates_warnings_only_records`
  - `test_recompute_tiers_leaves_records_with_other_reasons_unchanged`
- [ ] Commit: `fix(discovery-hardening): Phase 6 — advisory warnings, report-path load, tier back-fill`

### Phase 7: Curated resume lanes (+ docs tail)

- [ ] `src/job_hunt/generation.py`:
  - Add `CURATED_RESUME_LANES: Final[tuple[tuple[tuple[str, ...], str], ...]]` at module scope. AI lane first; wildcard default last.
  - Inline the lane-match logic at the call site in `generate_resume_variants` (no named helper for v1 — only two lanes; extract when a third lands). Logic is ~5 lines.
  - **Use `from .utils import repo_root`** for path resolution. Do NOT add a `repo_root` kwarg — that would conflict with the repo's established `repo_root()` utility (used 20+ places in `application.py`, `playbooks.py`, `confirmation.py`, `core.py`, `ingestion.py`).
  - When a curated resume is picked, the variant record gets `provenance: "curated"` and `curated_source: <relative path>`; skip the rendering step and copy the file content.
  - When a non-wildcard lane matches a lead but the source file is missing on disk, emit a `generation_warning` with code `curated_source_missing` rather than silently falling back to the template. This preserves audit trail per plan's anti-pattern against silent degradation.
- [ ] `schemas/generated-content.schema.json`:
  - Extend `provenance` enum to include `"curated"` (additive; existing records still validate).
  - Add `"curated_source": {"type": "string"}` as an optional property.
- [ ] Tests in `tests/test_generation.py`:
  - `test_curated_lane_selects_ai_engineer_for_ai_titled_lead`
  - `test_curated_lane_wildcard_default_for_generic_swe`
  - `test_curated_lane_returns_none_when_source_missing_emits_warning`
  - `test_curated_variant_copies_file_byte_for_byte`
  - `test_curated_variant_record_has_correct_provenance_and_source`
- [ ] Commit the two updated profile reports (`docs/reports/profile-completeness.md`, `docs/reports/profile-document-audit.md`) in a small trailing docs commit: `docs(discovery-hardening): Phase 7 tail — profile reports + lane notes`.
- [ ] Feature commit: `feat(discovery-hardening): Phase 7 — curated resume lanes`

## System-Wide Impact

### Interaction graph

- Phase 2's UA swap fires on every `ingestion.fetch` call site (`ingest_url`, `_fetch_indeed_viewjob`, robots.txt loader in discovery). Trace: `discover_jobs` → `RobotsCache` → `fetch` → `urllib.urlopen`. All share the new headers.
- Phase 3's jitter wiring affects every `acquire()` call inside `discover_jobs` for the listed hosts. No change for greenhouse / lever.
- Phase 7's tier change is read by `application.generate_application_artifacts` and downstream playbook generation.

### Error propagation

- Phase 2 raises `IngestionError(error_code="rate_limited")` up to `ingest_url`, which already handles that error code in its `try/except` chain.
- Phase 5's `_fetch_indeed_viewjob` re-uses `fetch`'s error paths; its own parse failures fall back to the HTML branch without raising.

### State lifecycle risks

- Phase 4's `_run_source` lead patch mutates a JSON file **after** it was already written. If a downstream consumer is reading the lead mid-flight, they could see a pre-patch version. Mitigation: writes are atomic via `write_json`; consumers must not interleave reads with a discovery run (already an invariant).
- Phase 8's curated path *copies* the source file byte-for-byte. If the source file changes on disk between discovery and generation, the committed artifact represents the state at generation time — acceptable.

### API surface parity

- `fetch` and `DomainRateLimiter` are touched on core paths; no parallel interfaces (no ORM shortcuts or DSL wrappers).
- `generate_resume_variants` adds a `repo_root` kwarg with a default; no caller change required.

### Integration test scenarios

1. `discover_jobs` → `ingest_url` → `extract_lead` pipeline against a fixture Indeed viewjob HTML, asserting the resulting lead has real `title`/`company`/`location` and stopword-filtered `keywords`.
2. `generate_resume_variants` + `run_ats_check_with_recovery` + `_compute_tier` pipeline for a curated-lane lead, asserting `tier="tier_1"` when ATS returns warnings-only.
3. Discovery run with a mocked 429 response → raised `IngestionError` with `rate_limited` code visible in the run summary (not silently swallowed).
4. DomainRateLimiter: two synchronous `acquire(indeed.com/...)` calls produce interval ≥ 3.0 and ≤ 7.0 (sample via `pick_interval`, not wall clock).
5. Legacy Indeed fixture + new Indeed fixture both parse to populated `IndeedJobPosting` with `employer_name` set.

## Acceptance Criteria

### Functional

- [ ] `test_discovery_user_agent_constant_single_sourced` passes against every tree state during the phase rollout.
- [ ] `DISCOVERY_USER_AGENT` contains the Chrome 131 UA; `ingestion.fetch` sends the same UA plus browser-shape `Accept` / `Accept-Language`.
- [ ] `DomainRateLimiter.set_human_jitter("indeed.com", 3.0, 7.0)` is installed by `discover_jobs`; verified by a test that asserts each entry in `HUMAN_JITTER_HOSTS`.
- [ ] Indeed card parsing succeeds on both 2025 (`<h2 class="jobTitle">`) and 2026 (`<span id="jobTitle-{jk}">`, `aria-label="full details of …"`) markup.
- [ ] `ingest_url` dispatches `indeed.com/viewjob` URLs to `_fetch_indeed_viewjob`; JSON-LD path and `#jobDescriptionText` fallback both produce populated `LeadPosting`.
- [ ] `extract_lead` excludes stopwords from `keywords`; ATS stuffing threshold is 0.10.
- [ ] `_compute_tier` returns `tier_1` when ATS status is `warnings`; `_run_ats_check` loads the report from `ats_check.report_path`.
- [ ] Curated resume lane is selected when lead title matches; variant record has `provenance="curated"` and `curated_source`.

### Non-Functional

- [ ] No new dependencies; stdlib `urllib` + `re` + `random` + `email.utils` + `html` + `json` only.
- [ ] No network calls in tests; all fixtures pinned under `tests/fixtures/`.
- [ ] CLI surface remains backward compatible: existing `discover-jobs`, `ingest-url`, `generate-resume-variants`, `apply` subcommands accept the same flags. One new subcommand (`recompute-tiers` or `check-integrity --recompute-tiers`) is additive.
- [ ] Existing `data/leads/*.json` and `data/generated/resumes/*.json` records remain valid against the updated schemas (additive-only property changes; existing enum values preserved).

### Quality gates

- [ ] Each phase commits green (`python3 -m unittest discover tests` passes).
- [ ] Phase 0 runs alone before Phase 2 to prove the test-only change is sufficient.
- [ ] Each phase has at least one targeted test.

## Success Metrics

- CI passes on the full tree.
- Discovery run against Indeed produces leads with populated `title`/`company`/`location` and no `"Retry-After"` warnings for at least one full day of use.
- Wall-clock: `discover-jobs` run takes roughly 3-5 minutes when Indeed + LinkedIn are both in the source list. Acceptable for a 1-2x daily operator flow. Acceptance: the command emits progress logging during long pacing waits so the user does not think the process is hung.
- ATS tier distribution shifts toward `tier_1` (warnings were over-weighted before); no legitimate `tier_2` promotions regress to `tier_1` (spot-check via `_compute_tier` unit tests over the existing fixture set).
- Existing `data/applications/*-status.json` records previously demoted to `tier_2` **only** because of `ats_status:warnings` are re-stamped to `tier_1` after a single run of the back-fill subcommand.
- Curated resume lane hit rate > 80% on the candidate's real lead inbox (spot-check post-merge).

## Dependencies & Risks

- **Indeed markup drift risk**: Phase 4's three-alternation regex tolerates two old shapes + the current one, but Indeed reshuffles roughly yearly. Mitigation: fixture-driven tests mean a future fourth shape requires a single fixture addition.
- **ToS risk**: UA swap + jitter reduce automation signatures but don't bypass Indeed's behavioral detection. Per `human-in-the-loop-on-submit-as-tos-defense.md`, humans still click Submit. Discovery/ingestion are read-only on a public listing site; this plan does not change the compliance boundary.
- **Curated resume drift**: Phase 8's curated path bypasses the scoring logic in `generate_resume_variants`, so a lead-specific keyword miss won't be caught by keyword-coverage checks at generation time. ATS check still runs on the copied markdown; low-coverage curated resumes surface as `keyword_coverage_below_target` warnings, which tier_1 tolerates (intended behavior).
- **Test failures from stopword filter**: `extract_lead`'s `keywords` are used downstream in resume keyword matching and ATS coverage. If any existing test pins specific keywords that are now filtered, Phase 6 will surface them. Budget one commit for test adjustments if needed.

## Files Likely To Change

- `src/job_hunt/discovery.py`
- `src/job_hunt/net_policy.py` (new `DISCOVERY_USER_AGENT` module-level constant + `set_human_jitter`)
- `src/job_hunt/ingestion.py`
- `src/job_hunt/indeed_discovery.py`
- `src/job_hunt/core.py`
- `src/job_hunt/ats_check.py`
- `src/job_hunt/application.py`
- `src/job_hunt/generation.py`
- `schemas/generated-content.schema.json` (`provenance: "curated"`, `curated_source`)
- `schemas/lead.schema.json` (optional `employer_name` field)
- `tests/test_discovery.py`
- `tests/test_net_policy.py`
- `tests/test_ingestion.py`
- `tests/test_indeed_discovery.py`
- `tests/test_discover_jobs.py`
- `tests/test_ats_check.py`
- `tests/test_phase4_application.py`
- `tests/test_generation.py`
- Optionally a new `tests/test_discovery_hardening.py` for cross-module integration tests
- Optionally a fixture under `tests/fixtures/` for the Indeed viewjob JSON-LD page
- `docs/reports/profile-completeness.md`
- `docs/reports/profile-document-audit.md`

## Anti-Patterns To Avoid

- Do not land any of Phases 2-8 before Phase 0's test fix; CI will go red intermittently.
- Do not combine Phases 6 and 7 — they touch different subsystems and one might need backout.
- Do not introduce a headless browser or a third-party HTML parser; the JSON-LD path + targeted regex is sufficient.
- Do not hardcode the Chrome UA string in two places; define once in `net_policy.py` and import where used.
- Do not remove the legacy `<h2 class="jobTitle">` branch from `_TITLE_RE` — Indeed still serves that shape for some geographies / A-B cohorts.
- Do not move `_KEYWORD_STOPWORDS` inside `extract_lead`; policy constants stay at module scope.

## Test Strategy

Follow the repo's flat `unittest.TestCase` pattern. Each phase adds tests in the nearest existing test module or — for cross-phase integration — a new focused test file. Tests must:

- Use `tempfile.TemporaryDirectory` for on-disk state.
- Pin HTML / JSON fixtures under `tests/fixtures/` (add a subdirectory if needed).
- Avoid network: all `urllib` calls stubbed via `unittest.mock.patch`.
- Prefer structural assertions (fields populated, constants match, tier is `"tier_1"`) over byte-for-byte snapshots.

## Open Questions

- Should the jitter policy extend to international Indeed hosts (`indeed.co.uk`, `ca.indeed.com`, etc.)? `registered_domain` buckets `*.indeed.co.uk` under `co.uk`, which would not match a `set_human_jitter("indeed.com", ...)` call. v1 stays US-only; a follow-up can add per-suffix coverage once the candidate's real lead mix is known.
- Long-term, should `HUMAN_JITTER_HOSTS` be driven from a profile/config file rather than hard-coded in `discover_jobs`? Not v1.
- Should Phase 6's back-fill run once (manually invoked) or be wired into `check-integrity` as an auto-remediation? v1 ships as a standalone subcommand; auto-remediation is a later decision.

## Resolved Design Decisions

- **Shared Chrome UA constant lives in `net_policy.py`**: both `discovery.py` and `ingestion.py` already import from it; no circular dependency. `RobotsCache` keeps its UA-agnostic constructor but defaults to this constant in `discover_jobs`.
- **`set_human_jitter` calls are inline in `discover_jobs`**, not hidden behind a `HUMAN_JITTER_HOSTS` tuple. Two call sites is not enough to earn a constant; tests assert the calls, not the constant.
- **Indeed `MAX_PAGES_PER_RUN = 2`** is the v1 choice; revisit if daily lead volume falls below the ≤20/day ToS-defense envelope.
- **Curated resume lanes dispatch by title keyword alone** in v1; future slice can add lane fields on the profile to support explicit overrides.
- **`repo_root` uses the existing `utils.repo_root()` utility**, not a new kwarg. This avoids inventing a second convention that conflicts with the 20+ existing call sites.
- **`random.uniform` is correct for jitter** — non-cryptographic use; Python's own docs scope `secrets` to auth/tokens.
- **`email.utils.parsedate_to_datetime` is the canonical HTTP-date parser** for the Retry-After fallback path — no third-party dependency needed.
- **Phase 1+3 merged, Phase 9 folded** into Phase 7's tail per the deepen-plan simplicity pass. Ten phases → eight.
- **Mutate-after-write replaced with override-hints into `ingest_url`** per the architecture review: single atomic write, no half-patched lead state on crash.
- **Back-fill ships as an explicit subcommand**, not auto-run on import. Keeps the Phase 6 tier change composable with the integrity-check workflow.

## Recommended Execution Order

1. Phase 0 (UA single-sourcing test retarget) — alone, unblocks CI.
2. Phase 1 (jitter primitive + wiring).
3. Phase 2 (Chrome UA + RFC 9110 Retry-After + caller audit + RobotsCache thread-through).
4. Phase 3 (Indeed parser hardening + override-hints).
5. Phase 4 (Indeed viewjob JSON-LD ingestion).
6. Phase 5 (keyword hygiene coupled change).
7. Phase 6 (tier advisory + ATS recovery bug fix + tier back-fill).
8. Phase 7 (curated resume lanes + profile-report tail).

## Sources & References

### Internal

- Active plan: `docs/plans/2026-04-18-001-feat-cover-letter-lanes-plan.md` (cover-letter-lanes, Phase 5 explicitly deferred)
- Convention template: same file, for phase shape + `✅` markers
- Failing test: `tests/test_discovery.py:421` (`DiscoveryUserAgentTest`)

### Solutions (carry-forward)

- `docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md`
- `docs/solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md`
- `docs/solutions/workflow-issues/integrate-review-findings-into-deepened-plan-without-split-brain.md`

### Current WIP (git working tree, 2026-04-18)

- 553 lines across 8 `src/` files, 2 `docs/reports/` markdown files
- `git diff HEAD -- src/job_hunt/{discovery,net_policy,ingestion,indeed_discovery,core,ats_check,application,generation}.py docs/reports/*.md`

## Acceptance Summary

Ready to ship when:

- All eight phases have landed as separate commits following the repo's `feat(discovery-hardening): Phase N — …` convention (Phase 0 uses `test(...)`; Phase 6 uses `fix(...)`; Phase 7 has a `feat(...)` commit plus a short `docs(...)` tail for profile reports).
- Full `python3 -m unittest discover tests` is green.
- `git status` is clean.
- PR description references this plan and links each commit to the corresponding phase.
- `recompute-tiers` (or equivalent) has been run once against existing `data/applications/*-status.json` to back-fill records that were demoted solely due to advisory warnings.
