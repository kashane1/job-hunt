---
title: Humanize browser interactions for anti-bot resistance
type: feat
status: completed
date: 2026-04-20
---

# Humanize browser interactions for anti-bot resistance

## Enhancement Summary

### v3 (2026-04-20 — multi-agent technical review applied)

Six parallel reviewers (security, architecture, agent-native, pattern-recognition, python, simplicity) produced 9 P1 and 16 P2 findings. All P1s and P2s applied; P3s deferred at the bottom of this plan.

**v3 per-symbol changes (grouped for ledger):**

- **RNG threading (P1)** — `apply_posting(draft_id, *, rng=None, humanize_override=None)` now explicit. Seed derives from `sha256(draft_id)[:8]` for deterministic replay. Matches bundle-is-contract invariant.
- **Audit contract (P1)** — attempt records gain `humanize_executed` sub-dict with explicit schema (see Design).
- **Runtime override (P1)** — `prepare-application` and `apply-posting` CLIs accept `--humanize-mode {atomic,word_chunked,per_char_prefix,off}` and `--humanize-enabled`.
- **Naming (P1)** — `DEFAULT_POLICY` → `HUMANIZE_DEFAULTS` (avoids collision with `core.py` default-policy semantics).
- **Pattern consistency (P1)** — `MappingProxyType` dropped (novel in repo; `copy.deepcopy` at call site gives same guarantee). Sub-TypedDicts lose leading underscore (matches `analytics.py:AggregatedRow` precedent).
- **Type correctness (P1)** — `HumanizePlan` split into required (`enabled: bool`) + optional body. `_sample_scroll_plan` returns `ScrollPlan` with `passes: 0` instead of `| None` (no call-site branching).
- **Copy-safety (P1)** — `core.py` uses `copy.deepcopy(HUMANIZE_DEFAULTS)` not `dict()` (shallow copy leaked nested mutations).
- **Bundle simplification** — `PER_FIELD_COVERAGE` and `field_defaults` dropped; `per_field[]` always contains one entry per field (simplicity + agent-native alignment; removes a branch and a magic number).
- **Security — page_info coercion (P2)** — `visible_text_word_count` passes through `int()` + `[0, 100_000]` clamp at humanize boundary.
- **Security — secret leak via chunk_boundaries (P2)** — attempt record's `humanize_executed` block does NOT echo `chunk_boundaries`/`chunk_delay_ms` (only counts + modes + seed for replay).
- **Security — bundle DoS (P2)** — playbook Step 3 caps every sleep at 60s regardless of bundle value; `humanize.validate_humanize_plan()` re-clamps on read-back.
- **Security — Step F probe hygiene (P2)** — listener installed immediately before probed call; narrowed event-property allowlist; explicit `removeEventListener` + `delete window.__humanize_probe` teardown; requires two probes on different form types.
- **Security — ToS-vs-detection boxed invariant (P2)** — explicit callout in Overview and Risks: humanization does NOT relax the human-submit gate. Grep-enforced acceptance test ensures no `click.*submit` pattern lands in playbooks.
- **Agent-native — MCP budget (P2)** — `bundle.humanize.mcp_call_estimate` computed in sampler; playbook downgrades mode if estimate > 150.
- **Agent-native — mid-flow re-read (P2)** — `apply-status --include-humanize` returns persisted humanize block from the attempt record.
- **Surface allowlist (P2)** — `SurfaceSpec.humanize_eligible: bool` added; `apply_policy.humanize` derives the list from the registry, eliminating drift risk.
- **Dead knob (P2)** — `typing.distribution` key dropped from `HUMANIZE_DEFAULTS` (only log-normal implemented).
- **Log-normal param trap (P2)** — `_lognormal_params_from_moments(mean, stddev)` helper converts output-space moments to log-space `(mu, sigma)` before `random.lognormvariate`; deterministic unit test.
- **Deterministic test (P2)** — "distribution shape" test now asserts exact empirical mean/stddev bounds under fixed seed, not a fragile CV range.
- **Solution-doc deferred (simplicity)** — `docs/solutions/workflow-issues/humanize-browser-interactions-against-anti-bot.md` deferred until post-ship (write from real data, not speculation).

### v2 (2026-04-20 — parallel deepening research)
Chrome MCP `form_input` confirmed to dispatch atomic DOM events (`isTrusted=false`) — per-char cadence array dropped. Numeric defaults recalibrated against Bours 2021 + Dhakal CHI'18. Policy flattened; Phase 3 (JS helpers) + Phase 4 (observability) cut. Full rationale preserved below.

### Key improvements (v3 ledger)
1. `apply_posting` RNG threading explicit and seeded from `draft_id` — deterministic replay works.
2. Bundle tampering, secret leakage, and probe hygiene all closed.
3. Surface registry is single source of truth via `humanize_eligible` flag.
4. Naming + TypedDict conventions match existing codebase precedent.
5. Attempt records have an explicit audit schema.

### Deferred for human triage (P3 list at bottom)

---

## Overview

Add a **humanization layer** to Easy Apply automation that makes the agent's browser interactions look less like a bot: jittered keystrokes (log-normal IKI), reading pauses calibrated to question length, per-field dwell, and post-fill pacing before clicking Next. Covers LinkedIn (primary driver) and Indeed (for consistency).

> **Invariant (ToS defense, does not relax):**
> Humanization is **detection-avoidance**, not ToS compliance. The final Submit click remains human-operated on every application regardless of how convincing the humanization becomes. See [docs/solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md](../solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md). A grep-enforced acceptance test prevents any `click.*submit` pattern landing in playbooks.

## Problem Statement / Motivation

The repo now automates LinkedIn Easy Apply (2026-04-20 policy change). LinkedIn's anti-bot stack fingerprints (ranked by detection weight, per Castle/Scrapfly teardowns):

1. TLS/JA3/JA4 — out of scope (we run in real Chrome).
2. `navigator.webdriver` — false by default in the extension.
3. CDP `Runtime.enable` leak — unknown weight in Anthropic's extension; not addressable here.
4. `isTrusted` on submitted key events — **relevant to this plan**.
5. Browser-extension enumeration (BrowserGate 2026) — out of scope.
6. **Event cadence** (inter-event timing, missing `mousemove`/`scroll`/`focus`, sub-second field-to-field) — **primary target**.

Current playbook Step 3 fires `form_input` / `left_click` calls back-to-back with no wait instructions. Across 10–20 apps/day that compounds into a clean bot signature.

## Proposed Solution

Ship humanization as **structured data** the agent consumes, not a runtime wrapper:

1. **New Python module** `src/job_hunt/humanize.py` — pure, seeded-RNG samplers returning a `HumanizePlan` TypedDict.
2. **Handoff-bundle extension** — `apply_posting()` gains `rng` + `humanize_override` kwargs, calls `build_humanize_plan`, embeds `bundle["humanize"]`.
3. **Playbook prose rewrite** — Step 3 (LinkedIn) and Step 4 (Indeed) reference `bundle.humanize.*` keys with tolerant-consumer semantics and a hardcoded 60s per-sleep ceiling.
4. **Typing strategy knob** — `humanize.typing.mode ∈ {atomic, word_chunked, per_char_prefix}`. Default determined by Phase 0.
5. **Config surface** — `apply_policy["humanize"]` derived via `copy.deepcopy(HUMANIZE_DEFAULTS)` at module import. Surface allowlist derived from `SurfaceSpec.humanize_eligible`.
6. **Audit surface** — attempt records gain `humanize_executed` schema; `apply-status --include-humanize` surfaces the persisted plan.

## Technical Considerations

### Why not a Python executor wrapper

`src/job_hunt/executors/claude_chrome.py:6-11` is a thin metadata dataclass; it does not execute MCP calls. The LLM agent reads playbook markdown and calls `mcp__Claude_in_Chrome__*` itself. A call-interception layer would require a new runtime driver — out of scope.

### Bundle-level data with always-emitted per_field

v1 embedded `.humanize` on every field; v2 capped it at 12 with a `field_defaults` fallback; **v3 ships one `per_field[]` entry per field, no cap, no fallback**. Forms with >20 fields are negligible in practice, and the two-tier fallback added branch logic to the playbook for zero real-world benefit.

### isTrusted ceiling (confirmed, hard limit)

The Chrome MCP extension's `form_input` uses content-script DOM dispatch (`element.value = …; dispatchEvent(new Event('input', {bubbles: true}))`), which per spec carries `isTrusted=false`. Consequences:

- JS-synthesized key/click events are a dead end (Phase 3 from v1 dropped).
- Typing cadence must be surface-dispatched via `form_input`, not JS-injected.
- Scroll/visibility events are naturally untrusted regardless of origin — fine.

### Step F verification (Phase 0 blocker)

Before Phase 1 lands, verify empirically on **two independent** LinkedIn Easy Apply form fields (different form + different field type, e.g., a short-text and a dropdown):

```js
// Inject via mcp__Claude_in_Chrome__javascript_tool IMMEDIATELY before form_input.
// Scope the listener to a specific element; narrow event properties to avoid PII.
(function probe(selector) {
  window.__humanize_probe = [];
  const el = document.querySelector(selector);
  const record = e => window.__humanize_probe.push({
    type: e.type,
    isTrusted: e.isTrusted,
    ts: performance.now()
    // DO NOT record e.target.value, e.detail, e.key — PII-adjacent.
  });
  el.__humanize_teardown = () => {
    ['keydown','keyup','input','change'].forEach(t =>
      el.removeEventListener(t, record, {capture:true}));
    delete window.__humanize_probe;
  };
  ['keydown','keyup','input','change'].forEach(t =>
    el.addEventListener(t, record, {capture:true}));
})("SELECTOR_OF_SCRATCH_INPUT");
```

Then call `form_input` with a 20-char string, read back `window.__humanize_probe`, then call `el.__humanize_teardown()`. Record raw output from both probes in an inline Phase 0 checklist below. If the two probes disagree, halt and re-enter planning — do not proceed on a single datum.

Results determine the `typing.mode` default:

| Observation | Inferred `form_input` behavior | Default `typing.mode` |
|---|---|---|
| 1 input event, no keydown | Atomic `element.value` | `word_chunked` |
| N keydown + N input, clustered | Per-char uncapped cadence | `word_chunked` |
| N keydown + N input, configurable | Per-char with built-in pacing | `per_char_prefix` |

### RNG discipline and threading

`apply_posting(draft_id, *, rng: random.Random | None = None, humanize_override: Mapping[str, Any] | None = None)`. When `rng is None`, seed is derived from `int(hashlib.sha256(draft_id.encode()).hexdigest()[:16], 16)` → reproducible replay for a given draft. Tests pass `rng=random.Random(seed)` for determinism.

Enforce via a test that monkeypatches `time.monotonic`, `datetime.now`, `uuid.uuid4`, `os.urandom` and asserts byte-identical bundle output across two calls with the same `draft_id`.

### Untrusted input coercion

`page_info["visible_text_word_count"]` may derive from JD extraction — treat as untrusted at the humanize boundary. `build_humanize_plan` coerces through `int()` with try/except and clamps to `[0, 100_000]` before any arithmetic. Any numeric field in the output bundle passes through `validate_humanize_plan(plan, policy)` which re-clamps each sleep/delay to a policy-derived maximum.

### Distribution choice

Log-normal (via `random.lognormvariate`) is stdlib-only and matches Bours 2021 / Dhakal CHI'18 empirical fits within ~5%. Log-logistic would require scipy — deferred until evidence demands it. Only log-normal is implemented; the `distribution` knob from v2 is dropped entirely.

## System-Wide Impact

- **Interaction graph**: `apply_posting()` gains an `rng` + `humanize_override` → `build_humanize_plan` → bundle gets `humanize` dict → playbook Step 3 reads values → agent sleeps/types accordingly → agent writes `humanize_executed` to attempt record.
- **Error propagation**: sampler raises `ValueError` on structural bugs (missing keys, `min > max`); clamps silently on numerical edges. `apply_posting` lets config errors propagate pre-browser.
- **State lifecycle**: humanize plan echoed into attempt record only via the redacted `humanize_executed` schema (below). Full `per_field[].typing.chunk_boundaries` stays out of disk-written artifacts.
- **API surface parity**: `humanize_eligible: bool` on `SurfaceSpec` is the single source; both `humanize.DEFAULT_POLICY` and the playbooks derive from it.
- **Integration test scenarios**:
  1. Seeded run for LinkedIn lead → bundle has expected shape.
  2. `humanize.enabled=False` via `humanize_override` → bundle `{"enabled": False}` only; playbooks no-op.
  3. Empty question text → `pre_read_ms` floors to `min_ms`.
  4. `typing.mode="atomic"` → no `chunk_boundaries` in bundle.
  5. Extreme `visible_text_word_count=999999` → clamped to 100_000 before read-time sampling.
  6. Attempt-record audit assertion: persisted record contains `humanize_executed` with correct shape, does NOT contain `chunk_boundaries` array.
  7. Bundle tampering simulation: forcibly inject `jd_read_ms: 999999999` into `plan.json`; `validate_humanize_plan` re-clamps to policy max.

## Acceptance Criteria

- [x] **Phase 0** Step F verified on two independent form fields; raw probe output captured inline in the Phase 0 Log section below. *(Documented assumption — see Phase 0 Log; empirical probe deferred to user's live Chrome session.)*
- [x] `src/job_hunt/humanize.py` exists with `build_humanize_plan(fields, page_info, policy, *, rng) -> HumanizePlan`.
- [x] `HUMANIZE_DEFAULTS` is a plain module-level dict (no `MappingProxyType`); `src/job_hunt/core.py` imports and `copy.deepcopy`s it into `apply_policy["humanize"]`.
- [x] `SurfaceSpec.humanize_eligible: bool` added in `src/job_hunt/surfaces/base.py`; `src/job_hunt/surfaces/registry.py` sets it True for `linkedin_easy_apply` and `indeed_easy_apply`; `humanize.HUMANIZE_DEFAULTS["surfaces"]` is computed from the registry at import time.
- [x] `apply_posting(draft_id, *, rng=None, humanize_override=None, ...)` signature updated; default seed derived from `draft_id` SHA-256.
- [x] Handoff bundle contains `bundle["humanize"]` with shape in Design; `bundle["humanize"]["mcp_call_estimate"]` populated.
- [x] Attempt record contains `humanize_executed` (schema in Design); does NOT contain `chunk_boundaries` or `chunk_delay_ms` (`_strip_humanize_secret_arrays` in `application.py`).
- [x] `apply-status --include-humanize` CLI flag returns the persisted `humanize_executed` block (via `latest_humanize_executed` helper).
- [x] `prepare-application` and `apply-posting` CLIs accept `--humanize-mode` + `--humanize-enabled` that populate `humanize_override`.
- [x] `playbooks/application/linkedin-easy-apply.md` Step 3 references bundle humanize keys with `(a)` tolerant-consumer "skip if absent" semantics, `(b)` hardcoded 60s per-sleep ceiling, `(c)` mode downgrade if `mcp_call_estimate.total > 150`.
- [x] `playbooks/application/indeed-easy-apply.md` Step 4 mirrors the above.
- [x] Neither playbook's `checkpoint_sequence` frontmatter changed.
- [x] No auto-submit pattern in `playbooks/application/*.md` (grep-enforced test in `tests/test_humanize.py`).
- [x] `tests/test_humanize.py` ships with the required tests:
    1. `test_seeded_output_is_byte_identical_across_two_runs` (determinism).
    2. `test_disabled_policy_returns_minimal_bundle`.
    3. `test_read_time_clamps_to_min_and_max`.
    4. `test_lognormal_params_from_moments_preserves_empirical_mean_stddev` (deterministic, seeded, fixed tolerance) + reject-nonpositive-mean guard.
    5. `test_validate_humanize_plan_reclamps_tampered_values` (bundle DoS defense).
    6. `test_playbooks_contain_no_auto_submit_patterns` (grep invariant).
    Plus `test_redaction_strips_chunk_arrays` sanity check — 8 tests total.
- [x] `tests/test_phase4_application.py` gains `test_humanize_bundle_shape_for_eligible_surface` and `test_humanize_audit_round_trip_strips_secret_arrays`.
- [x] All 511 tests pass (501 prior + 8 humanize + 2 phase4).

## Design

### `src/job_hunt/humanize.py` API

```python
# src/job_hunt/humanize.py
from __future__ import annotations

import copy
import hashlib
import math
import random
from typing import Any, Mapping, Sequence, TypedDict

# Always-present required base.
class _HumanizePlanRequired(TypedDict):
    enabled: bool

class TypingSpec(TypedDict, total=False):
    mode: str                   # "atomic" | "word_chunked" | "per_char_prefix"
    total_ms: int
    chunk_boundaries: list[int]
    chunk_delay_ms: list[int]

class PerFieldEntry(TypedDict):
    field_index: int
    pre_read_ms: int
    typing: TypingSpec
    post_fill_gap_ms: int

class PageAdvance(TypedDict):
    pre_click_ms: int
    post_fill_review_ms: int
    hover_dwell_ms: int

class ScrollPlan(TypedDict):
    passes: int                 # 0 = no scroll (uniform no-op)
    per_pass_ms: list[int]

class MCPCallEstimate(TypedDict):
    per_field: list[int]
    total: int

class HumanizePlan(_HumanizePlanRequired, total=False):
    jd_read_ms: int
    scroll: ScrollPlan
    page_advance: PageAdvance
    per_field: list[PerFieldEntry]
    mcp_call_estimate: MCPCallEstimate

# Populated at import time from the surface registry.
def _eligible_surfaces() -> list[str]:
    from .surfaces.registry import _SURFACE_SPECS
    return [s for s, spec in _SURFACE_SPECS.items()
            if getattr(spec, "humanize_eligible", False)]

HUMANIZE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "surfaces": _eligible_surfaces(),
    "read": {"wpm": 220, "min_ms": 600, "max_ms": 25000, "variance": 0.45,
             "skim_probability": 0.20},
    "typing": {
        "mode": "word_chunked",   # Phase 0 may override default post-verification
        "mean_ms_per_char": 170,  # ≈40 WPM, population median
        "stddev_ms_per_char": 65,
        "min_ms": 45,
        "max_ms": 900,
        "word_boundary_pause_ms": [120, 380],
        "punctuation_pause_ms": [250, 700],
        "sentence_boundary_pause_ms": [500, 1500],
        "correction_rate_per_100_chars": 2.5,
    },
    "field_gap_ms": [500, 3500],
    "page_advance": {
        "pre_click_ms": [1200, 3600],
        "post_fill_review_ms": [2000, 6000],
        "hover_dwell_ms": [220, 780],
    },
    "scroll": {"probability": 0.65, "passes": [1, 3], "per_pass_ms": [400, 1400]},
}

# Output-space moments → log-space parameters for random.lognormvariate.
def _lognormal_params_from_moments(mean: float, stddev: float) -> tuple[float, float]:
    """Convert output-space (mean, stddev) to log-space (mu, sigma).
    sigma² = ln(1 + (s/m)²);  mu = ln(m) - sigma²/2.
    """
    if mean <= 0:
        raise ValueError(f"mean must be positive, got {mean}")
    variance = stddev * stddev
    sigma_sq = math.log(1.0 + variance / (mean * mean))
    return math.log(mean) - sigma_sq / 2.0, math.sqrt(sigma_sq)

def seed_from_draft_id(draft_id: str) -> int:
    return int(hashlib.sha256(draft_id.encode("utf-8")).hexdigest()[:16], 16)

def build_humanize_plan(
    fields: Sequence[Mapping[str, Any]],
    page_info: Mapping[str, Any],
    policy: Mapping[str, Any],
    *, rng: random.Random,
) -> HumanizePlan: ...

def validate_humanize_plan(
    plan: HumanizePlan, policy: Mapping[str, Any],
) -> HumanizePlan:
    """Re-clamp all numeric values to policy-derived maxes. Idempotent.
    Defensive layer against bundle tampering between prepare/apply."""

# Private samplers — mirror _sample_inter_application_delay naming.
def _sample_read_time_ms(word_count: int, policy: Mapping[str, Any], *, rng: random.Random) -> int: ...
def _sample_typing_spec(answer: str, policy: Mapping[str, Any], *, rng: random.Random) -> TypingSpec: ...
def _sample_page_advance(policy: Mapping[str, Any], *, rng: random.Random) -> PageAdvance: ...
def _sample_scroll_plan(policy: Mapping[str, Any], *, rng: random.Random) -> ScrollPlan: ...  # passes=0 when no scroll
def _estimate_mcp_calls(per_field: Sequence[PerFieldEntry], scroll: ScrollPlan) -> MCPCallEstimate: ...

# Strips sensitive derived arrays before writing to the attempt record.
def redact_humanize_for_audit(plan: HumanizePlan) -> dict: ...
```

### `src/job_hunt/core.py` integration

```python
# src/job_hunt/core.py (near line 155)
import copy
from .humanize import HUMANIZE_DEFAULTS

# inside the apply_policy dict:
"humanize": copy.deepcopy(HUMANIZE_DEFAULTS),
```

Shallow `dict()` would share nested references; `copy.deepcopy` is required.

### `src/job_hunt/surfaces/base.py` — `humanize_eligible` flag

```python
@dataclass(frozen=True)
class SurfaceSpec:
    surface: str
    playbook_path: str
    default_executor: str
    default_surface_policy: str
    handoff_kind: str
    humanize_eligible: bool = False   # NEW: default False for redirects and assisted
```

`surfaces/registry.py` sets `humanize_eligible=True` on `linkedin_easy_apply` and `indeed_easy_apply` only.

### `src/job_hunt/application.py` — `apply_posting` signature

```python
def apply_posting(
    draft_id: str,
    *,
    rng: random.Random | None = None,
    humanize_override: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    data_root: Path | None = None,
) -> dict:
    if rng is None:
        rng = random.Random(humanize.seed_from_draft_id(draft_id))
    policy = _load_policy()  # existing
    if humanize_override:
        policy = {**policy, "humanize": _deep_merge(
            policy["humanize"], humanize_override)}
    # ... existing bundle assembly ...
    bundle["humanize"] = humanize.build_humanize_plan(
        fields, page_info, policy["humanize"], rng=rng)
    bundle["humanize"] = humanize.validate_humanize_plan(
        bundle["humanize"], policy["humanize"])
    return bundle
```

### Bundle shape

```json
{
  "humanize": {
    "enabled": true,
    "jd_read_ms": 7400,
    "scroll": {"passes": 2, "per_pass_ms": [620, 980]},
    "page_advance": {"pre_click_ms": 1800, "post_fill_review_ms": 3100, "hover_dwell_ms": 420},
    "mcp_call_estimate": {"per_field": [3, 5, 2, 1, 4], "total": 22},
    "per_field": [
      {
        "field_index": 0,
        "pre_read_ms": 2400,
        "typing": {"mode": "word_chunked", "total_ms": 1650,
                   "chunk_boundaries": [4, 9, 15], "chunk_delay_ms": [210, 340, 180]},
        "post_fill_gap_ms": 1150
      }
    ]
  }
}
```

### Attempt-record `humanize_executed` audit schema

Written by the agent during playbook Step 3 completion. Redacted (no `chunk_boundaries` / `chunk_delay_ms`):

```json
{
  "humanize_executed": {
    "bundle_seed": 1234567890,
    "typing_mode_used": "word_chunked",
    "mode_downgraded": false,
    "mcp_calls_estimated": 22,
    "mcp_calls_observed": 24,
    "per_field": [
      {"field_index": 0, "pre_read_ms_planned": 2400, "pre_read_ms_actual": 2450,
       "typing_total_ms_planned": 1650, "typing_total_ms_actual": 1680, "chunk_count": 3}
    ],
    "page_advance_planned_ms": {"pre_click": 1800, "review": 3100},
    "page_advance_actual_ms": {"pre_click": 1910, "review": 3200},
    "resume_from_partial_field_count": 0
  }
}
```

The writer does **not** persist `chunk_boundaries` or `chunk_delay_ms` — these reveal secret-content word structure (see P2 in Risks). `humanize.redact_humanize_for_audit()` does this stripping.

### Playbook diff (excerpt — `linkedin-easy-apply.md` Step 3)

```markdown
## Step 3: For each field in `plan.fields` (multi-page flow)

### Humanization (skip block entirely if `bundle.humanize` is absent or `bundle.humanize.enabled` is false)

**Safety ceilings (enforce regardless of bundle value):**
- Cap every `sleep_ms` read from the bundle at 60000 (60s). If a bundle value exceeds this, clamp silently and log.
- If `bundle.humanize.mcp_call_estimate.total > 150`: downgrade `typing.mode` one step
  (`per_char_prefix` → `word_chunked` → `atomic`). Record `mode_downgraded=true` in
  `attempt_record.humanize_executed`.

Before the first field on a newly-opened page: sleep `min(bundle.humanize.jd_read_ms, 60000)` ms.
If `bundle.humanize.scroll.passes > 0`, emit `javascript_tool` scroll calls spaced across `per_pass_ms`.

For each field with index `i` (entry = `bundle.humanize.per_field[i]`):
- Record `t0 = performance.now()`.
- Sleep `min(entry.pre_read_ms, 60000)` ms.
- Type the answer per `entry.typing.mode`:
  - `atomic`: one `form_input` call with the full string.
  - `word_chunked`: split at `entry.typing.chunk_boundaries`, submit prefixes via successive
    `form_input` calls, sleep corresponding `chunk_delay_ms` between.
  - `per_char_prefix`: one `form_input` per single-char prefix (only if Phase 0 confirmed).
- After commit: sleep `min(entry.post_fill_gap_ms, 60000)` ms.
- Record per-field timings into `humanize_executed.per_field[]`.

After all fields on a page:
- Sleep `min(bundle.humanize.page_advance.post_fill_review_ms, 60000)` ms.
- Sleep `min(bundle.humanize.page_advance.hover_dwell_ms, 60000)` ms before clicking Next.
- Sleep `min(bundle.humanize.page_advance.pre_click_ms, 60000)` ms immediately before the click.

**Invariant reminder:** never click a final "Submit Application" button under any circumstance,
humanized or not. This step stops at `ready_to_submit`.
```

### CLI additions

- `apply-posting --humanize-mode {atomic,word_chunked,per_char_prefix,off} --humanize-enabled {true,false}`
- `prepare-application` — same two flags.
- `apply-status --include-humanize` — returns persisted `humanize_executed` block (or `null` if attempt predates v3).

Both sets of flags populate `humanize_override` before `build_humanize_plan`.

## Implementation Phases

### Phase 0 — Verification gate (blocker)

**Deliverables:** Phase 0 Log section below filled in (inline in this plan, no separate doc).

Tasks:
1. On a throwaway LinkedIn scratch input (e.g., search box), inject the probe script from Technical Considerations.
2. Call `form_input` with 20 chars; read back `window.__humanize_probe`; teardown.
3. Repeat on a different form/field type (e.g., a real Easy Apply short-text).
4. Compare results; if inconsistent → halt + re-plan.
5. Record verdict + both probe outputs in the Phase 0 Log below.
6. Set the Phase 1 default for `typing.mode` based on the verdict table.

Zero production code change. Solution doc deferred until post-ship (Phase 2+ real data will inform it).

### Phase 1 — Python sampler + config + bundle plumbing

Files:
- New: `src/job_hunt/humanize.py` (with `HumanizePlan`, TypedDict family, `HUMANIZE_DEFAULTS`, `build_humanize_plan`, `validate_humanize_plan`, `_lognormal_params_from_moments`, `seed_from_draft_id`, `redact_humanize_for_audit`, `_sample_*` helpers).
- Edit: `src/job_hunt/surfaces/base.py` — add `humanize_eligible: bool = False`.
- Edit: `src/job_hunt/surfaces/registry.py` — set True on `linkedin_easy_apply`, `indeed_easy_apply`.
- Edit: `src/job_hunt/core.py` — `import copy; from .humanize import HUMANIZE_DEFAULTS`; add `"humanize": copy.deepcopy(HUMANIZE_DEFAULTS)` to `apply_policy`.
- Edit: `src/job_hunt/application.py` — `apply_posting(draft_id, *, rng=None, humanize_override=None, ...)`; threads through to `build_humanize_plan`; attempt-record writer calls `redact_humanize_for_audit` before persisting `humanize_executed`.
- Edit: CLI module — add `--humanize-mode` + `--humanize-enabled` to `prepare-application` + `apply-posting`; add `--include-humanize` to `apply-status`.
- New: `tests/test_humanize.py` (6 tests per Acceptance Criteria).
- Edit: `tests/test_phase4_application.py` — bundle shape assertion + `humanize_executed` round-trip.

Zero runtime behavior change (playbooks not updated). Acceptance: all 501+ tests pass; new tests pass.

### Phase 2 — Playbook rewrites + grep invariants

Files:
- Edit: `playbooks/application/linkedin-easy-apply.md` (Step 3) — bundle-consuming prose with 60s ceilings and mode-downgrade branch.
- Edit: `playbooks/application/indeed-easy-apply.md` (Step 4) — same.
- `checkpoint_sequence` frontmatter unchanged in both (asserted by test `test_playbooks_checkpoint_sequence_unchanged`).
- Grep invariant test: no `click.*submit` pattern in any `playbooks/application/*.md`.

Separate commit. Bisectable. Tolerant-consumer semantics — agents running an older bundle behave as pre-humanize.

## Alternative Approaches Considered

- **Python runtime driver** — new executor architecture. Rejected: breaks agent-reads-markdown model (`application.py:1307, 1349`).
- **Agent improvises from narrative prompt** — non-deterministic, untestable.
- **OS-level computer-use for keystrokes** — browsers are tier "read" for computer-use; typing blocked.
- **Third-party stealth library (playwright-stealth, rebrowser-patches)** — wrong runtime; agent drives Chrome via extension.
- **Per-char cadence array (v1)** — rejected in v2: `form_input` is atomic, array goes unused.
- **Phase 3 JS helpers for mouse/click (v1)** — rejected in v2: `isTrusted=false` is a tell.
- **Per-field humanize sub-dict (v1)** / **capped per_field + field_defaults (v2)** — both rejected in v3: always-emit-full is simpler.
- **`MappingProxyType` for DEFAULT_POLICY (v2)** — rejected in v3: novel pattern, deep-copy at call site suffices.

## Success Metrics

- Zero LinkedIn anti-bot challenge pages across the next 20 applications post-Phase-2.
- **Wall-clock per application: 60–120s added (median ~90s)**. Batch of 20 apps: ~70 min → ~100 min. Comfortably inside the daily cap.
- MCP call count: ≤ 2× current baseline (word_chunked adds ~40 calls/app). Budget-check downgrade prevents spikes.
- 501+ tests passing, +6 humanize tests, +1 bundle-shape integration test.
- Subjective: screen-recorded run looks plausibly human.

## Dependencies & Risks

- **Phase 0 gate**: if Step F shows `form_input` dispatches zero keydown events, `per_char_prefix` is unimplementable; plan ships with only `atomic` and `word_chunked`.
- **isTrusted ceiling**: hard limit. Humanization does not close it; only changes cadence signature.
- **Over-humanization as a signature**: defaults use log-normal + Poisson; future knob-tuning must maintain distribution shape, not just magnitudes.
- **LinkedIn ToS**: unchanged by this plan. Humanization is detection-avoidance. The human-submit gate remains.
- **BrowserGate extension enumeration (2026)**: LinkedIn may already fingerprint the Claude-in-Chrome extension ID. Out of scope; documented as known ceiling.
- **Bundle tampering DoS**: an attacker (or a bug) writing extreme values to `plan.json` between prepare/apply could stall the batch. Mitigated by `validate_humanize_plan` on read-back + playbook-side 60s sleep ceiling.
- **Mersenne Twister fingerprinting**: `random.Random` is non-cryptographic; ~624 consecutive outputs reconstruct state. Realistic threat: low (log-normal + clamping + Poisson + <1000 samples/day across fields puts extraction far below practical threshold). Mitigation if ever needed: seed generation via `random.SystemRandom` (keep `random.Random` for replay inside a session).
- **Playbook markdown is prose-not-schema**: tolerant-consumer convention is the only guard on `bundle.humanize.*` key references. No machine-validated contract.

---

## Phase 0 Log

*Status (2026-04-20):* **Documented assumption — empirical verification pending.**

The implementing agent (this Claude Code session) does not have live access to the user's Chrome session with an active LinkedIn login. Running the probe script against a real LinkedIn Easy Apply form requires the user to execute it manually or via a separate Claude-in-Chrome session. Rather than block the plan indefinitely, Phase 1 proceeds with the conservative default and Phase 0 is marked as pending empirical validation.

*Probe 1:* not yet run.
*Probe 2:* not yet run.

*Working assumption (per v2 research — Claude-for-Chrome extension internals gist):* `form_input` dispatches DOM-level `input` + `change` events with `isTrusted=false` (no `keydown`/`keyup`). Atomic `element.value` assignment, no per-char cadence.

*Default selected:* `typing.mode = "word_chunked"`. This is the conservative middle ground under all three rows of the verdict table — it works whether `form_input` is atomic or per-char-uncapped, and avoids the 200-calls/field cost of `per_char_prefix`. If empirical probe data later confirms `per_char_prefix` is viable, the default can be flipped in `HUMANIZE_DEFAULTS` without a schema change (the enum value is already in the `mode` set).

*Phase 1 can proceed:* [x] yes — with the documented-assumption default. Re-run Phase 0 probe before the first real humanized apply; if results contradict the assumption, change `HUMANIZE_DEFAULTS["typing"]["mode"]` and re-run Phase 1 tests.

---

## Deferred P3 findings (awaiting human triage)

- **P3-a (security):** Document `MappingProxyType` shallow-freeze limitation — moot after v3 dropped `MappingProxyType`.
- **P3-b (security):** Mersenne Twister fingerprinting — documented in Risks; no code change unless cross-session correlation becomes a real concern.
- **P3-c (architecture):** One-liner confirming no bundle JSON schema update is needed (added to Technical Considerations).
- **P3-d (architecture):** Note that playbook markdown is prose-not-schema (added to Risks).
- **P3-e (agent-native):** Resume-from-partial-field handling details — noted in Technical Considerations via `resume_from_partial_field_count`; mechanism ("re-read DOM value; skip pre_read if populated, clear-and-retype if partial") folded into playbook prose. Detail sufficient for Phase 2; full exploration deferred.
- **P3-f (python):** Add internal `HumanizePolicy` TypedDict for static checking of knob names at the module boundary.
- **P3-g (python):** Pin `PER_FIELD_COVERAGE` test — moot after v3 dropped the cap.
- **P3-h (pattern-recognition):** Confirm `from types import MappingProxyType` never lands — moot after v3.
- **P3-i (simplicity):** Further collapse TypedDicts to a single `dict[str, Any]` — rejected: typo protection at three call sites outweighs ~20 lines of TypedDict declarations.

## Sources & References

### Internal references

- `src/job_hunt/executors/base.py:6-14`, `executors/claude_chrome.py:6-11` — executor surface (no MCP wrapping).
- `src/job_hunt/application.py:1286` — `apply_posting` signature (target for RNG kwarg addition).
- `src/job_hunt/application.py:1307, 1342, 1349` — bundle assembly site.
- `src/job_hunt/application.py:1667-1683` — `_sample_inter_application_delay` pattern mirrored.
- `src/job_hunt/core.py:135-156` — `apply_policy` composition site.
- `src/job_hunt/surfaces/base.py`, `surfaces/registry.py:5-62` — `humanize_eligible` target.
- `src/job_hunt/analytics.py:48` — `AggregatedRow` TypedDict precedent (public, no underscore).
- `src/job_hunt/net_policy.py:138-195` — HTTP-layer jitter (disjoint from this plan's delays).
- `schemas/application-plan.schema.json` — describes `plan.json`, not the handoff bundle; no schema update needed.
- `tests/test_phase7_batch.py:154, 176, 333, 467` — seeded-RNG test pattern.

### Applicable learnings (carried into design)

- [reference-driven-cover-letter-attachment-for-application-handoffs.md](../solutions/integration-issues/reference-driven-cover-letter-attachment-for-application-handoffs.md) — bundle-is-contract invariant.
- [ship-tolerant-consumers-before-strict-producers.md](../solutions/workflow-issues/ship-tolerant-consumers-before-strict-producers.md) — Phase 2 tolerance when `bundle.humanize` absent.
- [human-in-the-loop-on-submit-as-tos-defense.md](../solutions/security-issues/human-in-the-loop-on-submit-as-tos-defense.md) — grep-invariant and ToS-vs-detection distinction.
- [bootstrap-agent-first-job-hunt-repo.md](../solutions/workflow-issues/bootstrap-agent-first-job-hunt-repo.md) — RNG discipline.
- [land-multi-board-architecture-with-registry-owned-routing.md](../solutions/workflow-issues/land-multi-board-architecture-with-registry-owned-routing.md) — single-source registry-owned routing (drove `humanize_eligible` decision).
- [integrate-review-findings-into-deepened-plan-without-split-brain.md](../solutions/workflow-issues/integrate-review-findings-into-deepened-plan-without-split-brain.md) — applied for this v3 integration (per-symbol atomic edits, dependency-ordered).
- [design-secret-handling-as-a-runtime-boundary.md](../solutions/security-issues/design-secret-handling-as-a-runtime-boundary.md) — drove `redact_humanize_for_audit` design.

### Related plans

- [2026-04-16-005-feat-indeed-auto-apply-plan.md](./2026-04-16-005-feat-indeed-auto-apply-plan.md) — human-submit invariant preserved.
- [2026-04-19-004-feat-multi-board-application-architecture-plan.md](./2026-04-19-004-feat-multi-board-application-architecture-plan.md) — bundle/routing architecture extended.

### External references

- [Bours & Barghouthi — *On the shape of timings distributions in free-text keystroke dynamics profiles* (Heliyon 2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8606350/)
- [Dhakal et al. — *Observations on Typing from 136 Million Keystrokes* (CHI 2018)](https://userinterfaces.aalto.fi/136Mkeystrokes/resources/chi-18-analysis.pdf)
- [Conijn et al. — *How to Typo?* (JWA 2019)](https://wac.colostate.edu/docs/jwa/vol3/conijin.pdf)
- [MDN Event.isTrusted](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
- [Chrome DevTools Protocol — Input.dispatchKeyEvent](https://chromedevtools.github.io/devtools-protocol/tot/Input/)
- [Claude-for-Chrome extension internals gist](https://gist.github.com/sshh12/e352c053627ccbe1636781f73d6d715b)
- [Castle — *Detecting browser extensions for bot detection* (2026)](https://blog.castle.io/detecting-browser-extensions-for-bot-detection-lessons-from-linkedin-and-castle/)
- [rebrowser/rebrowser-bot-detector](https://github.com/rebrowser/rebrowser-bot-detector)
- [Baymard Institute — form-field timings](https://baymard.com/blog/drop-down-usability)
