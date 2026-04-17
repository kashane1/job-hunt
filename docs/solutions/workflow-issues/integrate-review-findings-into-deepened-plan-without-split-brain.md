---
title: Integrate multi-pass review findings into a deepened plan without reintroducing split-brain
date: 2026-04-16
module: plan_integration
problem_type: workflow_issue
component: multi_pass_plan_lifecycle
symptoms:
  - Deepened plan passes a multi-agent technical review that produces 13+ concrete P1/P2 findings
  - Each finding offers 2-3 resolution options requiring an explicit decision before edits begin
  - Naive surgical-edit integration across 20+ sections silently drops cross-references between prose, code blocks, schemas, CLI surfaces, phase deliverables, acceptance criteria, and test names
  - Reviewer agents then rediscover the same split-brain pattern the batch-2 lesson already documented — just at a different layer of the plan lifecycle
  - Effort required to reconcile drift post-implementation is 10× the cost of catching it at plan-edit time
root_cause: Multi-pass plan lifecycles (plan → deepen → review → integrate → work) accumulate decisions as text in multiple locations within the same plan document. The review phase produces correct findings with correct remediation options, but the integration phase must simultaneously (a) pick between options, (b) propagate choice to prose, (c) propagate to code blocks, (d) propagate to schemas, (e) propagate to phase deliverables/tests/acceptance criteria. Doing five-way propagation per finding, across 13 findings, is 65 micro-decisions. Any miss creates a new split-brain bug at a finer granularity than the one the review itself caught.
tags:
  - workflow
  - plan-lifecycle
  - split-brain
  - multi-agent-review
  - decision-capture
  - technical-review-integration
severity: high
---

# Integrate multi-pass review findings into a deepened plan without reintroducing split-brain

## Problem

A plan authored via `/workflows:plan`, deepened via `/deepen-plan` (12 parallel research/review agents), and then technical-reviewed via `/workflows:review` (8 parallel specialized reviewers) produced 13 concrete P1/P2 findings, each with 2-3 proposed resolution options. Integrating the findings back into the plan touches ~20 sections: Enhancement Summary, New/Modified file lists, architecture-decisions prose, four major code blocks (utils, net_policy, ingestion, discovery, watchlist), CLI surface, five JSON Schemas, three Phase deliverable sections, three Phase test lists, three Phase acceptance lists, and the top-level Acceptance Criteria.

A naive integration — "edit each section surgically as I encounter the relevant finding" — would make ~65 micro-decisions (13 findings × 5 representations per finding) in one sitting, with no intermediate commitment of the Option 1/2/3 choice each finding offered. The result is the same failure mode batch 2's `reconcile-plan-after-multi-agent-deepening-review.md` lesson documented, just at a finer grain: prose claims a contract that the artifacts below it do not implement.

The companion lesson `review-deepened-plans-before-implementation.md` establishes WHY to run the review pass at plan-edit cost rather than implementation-rework cost. This lesson addresses the complementary question: HOW to integrate the review's output so the remediations actually land everywhere they need to.

## Root Cause

Multi-pass plan lifecycles accumulate three kinds of text that must stay synchronized:

1. **Prose claims** — Enhancement Summary, architecture-decisions paragraphs, risk mitigations, rejected-alternatives rationales.
2. **Concrete artifacts** — code blocks (module signatures, constants, dataclass bodies, method bodies), JSON Schemas (field shapes, enum values, regex patterns).
3. **Verification surfaces** — phase deliverable checklists, test names, acceptance criteria (functional / non-functional / quality gates).

A single finding typically touches all three. For example, "close the DNS-rebinding TOCTOU" requires: (1) prose paragraph explaining the pinned-HTTPS approach, (2) code block showing `_PinnedHTTPSConnection` with `server_hostname` / `check_hostname=True` / `CERT_REQUIRED`, (3) Phase 1 deliverable bullets, (4) 3-4 named tests, (5) functional + non-functional acceptance criterion entries.

If the integrator fixes one finding in prose only and forgets the code block, the plan looks coherent to a reader who skims the Enhancement Summary but fails an implementer who works from the code block. If the code block updates but the test name doesn't, an implementer writes the right code but can't verify it. This is structural split-brain: the document internally contradicts itself.

Review findings that offer multiple resolution options compound the problem. An integrator who hasn't recorded "Option 1 for finding #028" before editing will second-guess mid-edit, silently drift between options across representations, and produce an internally-inconsistent result.

## Solution

Use the todo ledger as the decision surface; edit the plan as the specification surface; keep the two strictly layered.

### Process changes

1. **Capture every review finding as a todo file BEFORE editing the plan.** The todo holds the problem statement, findings, proposed options, and a Recommended Action field. The file-todos skill's template already enforces this structure.
2. **Commit the Option 1/2/3 choice explicitly before starting plan edits.** For findings with multiple options, either fill in Recommended Action or add a consolidation paragraph listing choices. The decision ledger is now a file the editor can re-read rather than a memory the editor must retain.
3. **Edit the plan in dependency order, not in review-agent order.** Sequence: Enhancement Summary (top-level ledger) → New/Modified file lists → architecture-decisions prose → module-structure code blocks → CLI surface → JSON schemas → Phase deliverables/tests/acceptance → top-level Acceptance Criteria. This ordering ensures earlier sections don't reference symbols later sections haven't introduced yet.
4. **Per symbol/constant/enum/CLI flag, update every representation in one pass.** Five locations: prose explanation, code block definition, schema value, phase deliverable bullet, test name or acceptance criterion. Treat them as a single atomic edit of the SYMBOL, not five separate edits of five different sections.
5. **Use grep-enforceable invariants as a post-edit sweep.** Check for: lingering references to renamed symbols (e.g., `_DomainRateLimiter` after rename to `DomainRateLimiter`), lingering v2 paths (e.g., `data/discovery/runs/` after rename to `history/`), error codes in prose that don't appear in the frozenset, constants mentioned in acceptance criteria but not declared in code blocks. These sweeps catch the drift automatically.
6. **Rename todos pending → complete only after the plan edit is verified.** Remaining `pending` todos are outstanding scope the plan does not yet reflect.

### Key implementation pattern

**The dependency-ordered edit sequence:**

```
Enhancement Summary            (ledger — what changed and why)
    ↓
New/Modified file lists        (inventory — what files exist)
    ↓
Architecture decisions prose   (rationale — why the shape is this shape)
    ↓
Module structure code blocks   (contract — precise signatures)
    ↓
CLI surface                    (UX — command names/flags)
    ↓
JSON Schemas                   (validation — field shapes/enums/patterns)
    ↓
Phase N deliverables           (work items — what to build)
Phase N tests                  (verification — what proves it)
Phase N acceptance             (gate — what ships)
    ↓
Top-level Acceptance Criteria  (global gate — what the batch commits to)
```

**Per-symbol atomic edit:** For a new `DISCOVERY_USER_AGENT` constant introduced by a review finding, the edit touches:

- **Prose** (Architecture decisions): "User-agent is `job-hunt/0.3` — dropped non-resolving `+URL` fragment per review."
- **Code block** (module structure): `DISCOVERY_USER_AGENT: Final = "job-hunt/0.3"`.
- **Phase 1 deliverable**: `[ ] DISCOVERY_USER_AGENT constant declared`.
- **Phase 1 test**: `test_discovery_user_agent_constant_single_sourced`.
- **Acceptance criterion**: `DISCOVERY_USER_AGENT constant is the single source of truth (grep-enforced)`.

All five updated in one pass means the reader sees a consistent picture from any entry point.

### Why this worked

- **Todos serialize the decision**, so the editor doesn't have to choose-and-edit simultaneously.
- **Dependency-ordered editing** means later sections can reference symbols earlier sections have already established, not the other way around.
- **Atomic per-symbol edits** guarantee all five representations move together.
- **Grep-enforceable invariants** catch residual drift automatically — the kind of drift a human editor reliably misses at the end of a long sitting.
- **The Enhancement Summary functions as a ledger**: a reviewer skimming the top can verify each claimed change lands in the body by searching for the keyword.

### Concrete outcome from this session

- 13 P1/P2 review findings → 13 todo files with explicit Option choices committed.
- Plan v2 (1460 lines) → v3 (1932 lines) via ~12 targeted section edits.
- Post-edit grep sweep caught one stale `_DomainRateLimiter` reference in the high-level architecture diagram; fixed in one edit.
- No split-brain introduced at v3 — validated by keyword scan confirming every Enhancement Summary claim has at least one concrete landing place (code block, schema, deliverable, test, or acceptance criterion).

## Prevention

- **Always run `/workflows:review` on a deepened plan before `/workflows:work`.** The review is what produces the decision ledger the integrator consumes.
- **Never integrate review findings without capturing them as todos first.** Even if the integrator plans to apply them in the same session, the todo is the decision-commitment surface.
- **When a todo offers multiple resolution options, record the chosen option in the todo before editing the plan.** The Recommended Action field exists for this.
- **Treat every new symbol/constant/enum/flag as a five-representation atomic edit.** Prose, code block, schema, deliverable+test, acceptance.
- **Keep the Enhancement Summary as a running ledger of plan versions.** v1 → v2 → v3 → ... each adds a section summarizing what changed and why. This creates a per-version audit trail that makes future reviews trivial to scope.
- **Grep-enforceable invariants belong in Phase 1 tests, not just in prose.** A `test_<constant>_single_sourced` that greps the source tree catches drift the reviewer would otherwise have to catch manually.
- **Budget for the integration pass.** A deepened plan that receives 13 findings is not a 15-minute edit session. Budget proportional to findings × representation count.

## References

- Predecessor learning (batch 2, same root cause at deepen-pass layer): [docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md](reconcile-plan-after-multi-agent-deepening-review.md)
- Companion learning (why to run the review): [docs/solutions/workflow-issues/review-deepened-plans-before-implementation.md](review-deepened-plans-before-implementation.md)
- Extend-CLI discipline (schema evolution + import shape): [docs/solutions/workflow-issues/extend-cli-with-new-modules-without-breaking-backward-compat.md](extend-cli-with-new-modules-without-breaking-backward-compat.md)
- Applied artifacts from this session:
  - `docs/plans/2026-04-16-004-feat-active-job-discovery-plan.md` (v1 → deepened v2 → v3 after review integration)
  - `todos/028-complete-p1-ip-pinning-tls-sni-redirect-specifics.md` through `todos/040-complete-p2-robots-cache-poisoning-and-dir-fsync.md` — 13 P1/P2 findings applied
  - `todos/041-pending-p3-split-brain-residuals-and-doc-consistency.md`, `042-pending-p3-simplicity-deferrals-triage.md`, `043-pending-p3-naming-and-typing-nits.md` — 3 P3s remain for triage

## Compound Workflow Notes

This solution doc was produced via `/workflows:compound` on the integration work itself. Phase 1 parallel subagents were intentionally skipped because the orchestrating conversation already held complete in-session context (conversation length: multi-turn review + integration cycle). Spawning 5 subagents to re-derive the analysis would have duplicated memory already present and added latency without adding perspective. This deviation from the compound skill's default 5-subagent Phase 1 is worth recording: the workflow is a means to the documentation, not an end in itself. When full context is in-session, write directly; spawn subagents when their independent read across a fresh context window adds signal.
