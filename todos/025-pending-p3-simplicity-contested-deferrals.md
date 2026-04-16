---
status: pending
priority: p3
issue_id: "025"
tags: [code-review, simplicity, contested, batch-2]
dependencies: []
---

# Simplicity reviewer flagged several items as over-engineered; other reviewers defend them

## Problem Statement

The simplicity reviewer flagged 6 deepening additions as over-engineered for a single-user CLI. Security, data-integrity, and performance reviewers independently defend several of these. This todo captures the contested items so the user can make a deliberate call.

## Findings

### Contested items

| Item | Simplicity says | Defenders say |
|---|---|---|
| Intake pending/processed/failed lifecycle | 3-dir state machine for "fetch + parse + write lead" is overkill; simple tempfile + delete-on-success suffices | Data integrity: lifecycle prevents false-failure records (todo 016) |
| Two-phase ATS write (pending → status) | Single write with try/except is simpler; crash window is milliseconds | Python review: crash-safe with recovery via check-integrity is real value (todo 021) |
| `fingerprint_version` day-1 | Canonicalizer shipped once, no history; YAGNI | Data integrity: agrees, and todo 022 recommends defer |
| ThreadPoolExecutor batch parallelization | 50-URL batch is "a coffee break"; serial is ~5 LOC | Performance: 500s vs 100s is a real UX difference |
| Full SSRF matrix + redirect handler | User is attacker? This is a personal tool. Scheme allowlist + localhost block is enough | Security: AWS metadata is real, DNS rebinding via malicious DNS is real (todo 014) |
| `AggregatedRow` TypedDict | All 3 consumers in same file; docstring suffices | Python review: pins schema, prevents drift (todo 020) |

### Uncontested simplicity items

These the simplicity reviewer flagged with no defense from other agents:

1. **Enhancement Summary has become 50+ lines of meta-commentary before Overview** — readers scroll past changelog before reaching the actual plan. Recommendation: collapse to a 5-line deepening changelog.

2. **`ats_check.status: "check_failed"` surfacing not explicitly in check-integrity deliverable** — data integrity reviewer wanted this added; simplicity reviewer didn't object.

3. **`_intake/failed/` unbounded growth, no retention policy** — data integrity reviewer flagged; simplicity-wise, a simple max-age sweep is trivial. Worth adding.

## Proposed Solutions

### Option 1: Accept defenders' positions for contested items; fix uncontested ones (Recommended)

**Contested items — retain current plan:**
- Keep intake lifecycle (todo 016 will fix the bug without removing the lifecycle)
- Keep two-phase ATS write (todo 021 fixes language and extracts to helper)
- Defer `fingerprint_version` (todo 022 recommends)
- Keep ThreadPoolExecutor (todo 016 fixes race conditions)
- Keep SSRF matrix (todo 014 closes remaining gaps)
- Add `AggregatedRow` TypedDict (todo 020)

**Uncontested items — fix:**
- Collapse Enhancement Summary to a 5-line deepening changelog. Move detail into phase-level research insights where they're more useful.
- Extend `check-integrity` to surface `ats_check.status: "check_failed"`
- Add retention policy for `_intake/failed/` (max age 7 days, warn after)

**Effort:** Small (all three are ~10-line plan edits)
**Risk:** None

### Option 2: Full simplicity-first rewrite

Cut all 6 contested items and drop the lifecycle / two-phase pattern. Security and integrity reviewers would need sign-off. More work now, less code later, but accepts known gaps that other reviewers flagged as exploitable.

Not recommended unless the user explicitly values simplicity over defense-in-depth.

## Recommended Action

Option 1. Close the three uncontested items; keep the contested additions (they have defenders).

## Acceptance Criteria

- [ ] Enhancement Summary collapsed to ~5 lines; details in phase-level sections
- [ ] `check-integrity` surfaces `ats_check.status: "check_failed"` records
- [ ] `_intake/failed/` retention policy documented; `check-integrity` warns on files older than 7 days
- [ ] Contested items retained; their defenders' todos (014, 016, 020, 021, 022) close the specific gaps

## Work Log

### 2026-04-16 - Discovery

**By:** code-simplicity-reviewer (contested by 4 other reviewers)

**Actions:**
- Simplicity reviewer flagged 6 items as over-engineered
- Security, data-integrity, performance, and python reviewers defended 5 of them
- Consensus: retain the features, fix specific bugs, tighten the summary
