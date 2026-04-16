---
title: Review deepened plans before implementation to catch split-brain contradictions and security gaps at plan-edit cost
date: 2026-04-16
module: plan_review
problem_type: workflow_issue
component: multi_pass_plan_review
symptoms:
  - Enhancement Summary prose in a deepened plan claims N fixes but residual artifacts (Interaction Graph, code signatures, error-code enumerations, test names, TypedDict declarations) still contradict those claims
  - Latent runtime bugs in plan code blocks (keyword-arg mismatches, error codes promised but never emitted, schema fields referenced but missing) that would fail at implementation or acceptance-test time
  - Security sections claim hardening but have implementation gaps (IPv4-only IP validation, DNS-rebinding TOCTOU, unbounded decompression) invisible in prose
  - Constant/test/symbol naming drifts between docstrings, code, deliverables, and acceptance criteria
  - Architectural invariants stated in prose but not encoded as testable deliverables
root_cause: Multi-pass plan workflows (plan → deepen → implement) accumulate decisions as prose annotations faster than concrete artifacts (schemas, code blocks, deliverable checklists, interaction graphs, test names, acceptance criteria) get updated to match. Without a dedicated review pass that treats the plan as a codebase with consistency invariants BEFORE implementation begins, split-brain contradictions survive into implementation where they cost roughly 10x more to fix (code rework + follow-up commits vs plan edits). The remedy is a proactive /workflows:review pass against the deepened plan, specifically hunting for the split-brain pattern documented in the prior-batch reconciliation solution.
tags:
  - workflow
  - plan-review
  - split-brain
  - prevention
  - proactive-lesson-reuse
  - compound-engineering
  - multi-agent-review
  - pre-implementation-gate
  - contradiction-scan
  - security
  - ssrf
  - schema-consistency
severity: high
---

# Review deepened plans before implementation

## Problem

Batch 1 of the job-hunt repo produced a postmortem solution — [reconcile-plan-after-multi-agent-deepening-review.md](./reconcile-plan-after-multi-agent-deepening-review.md) — that documented 8 split-brain contradictions between a deepened plan's prose and its implementation artifacts. Those findings were caught during review of the *implementation*, not the plan, which meant:

- Contradictions had already resolved themselves at random during coding
- Every ambiguity became a real bug in the codebase
- Fixes required code rework, follow-up commits, and a second review cycle
- The solution doc itself read as a postmortem ("here's what we missed") rather than a playbook ("here's how to avoid this")

The deepening pass itself contributes to this pathology: 8 parallel research agents inject ~18 enhancement decisions into an existing plan, but none of them reconcile their additions against each other or against the plan body. That's structural split-brain — multiple authoritative voices editing the same document without coordination.

## Root Cause

The review step sits in the wrong place in the workflow. Running review AFTER implementation means:

1. Every contradiction between prose and code blocks has already chosen a winner (whichever the implementer noticed first)
2. Every `...` or "TBD" in the plan has been filled in with an implementer's best guess
3. Every architectural split-brain has propagated across multiple files
4. Bugs cost real engineering cycles to unwind instead of minutes of plan editing

The cost differential is dramatic. Plan edits are text substitutions in one file. Code reworks require: change the code, update tests, update dependent modules, re-run review, sometimes re-deepen. A single P2 architecture finding caught during plan review might be a 5-line edit; the same finding caught during code review is typically a multi-file PR.

## Solution

**Insert `/workflows:review` BEFORE implementation, not after.**

### Revised workflow

```
/workflows:plan          → initial plan
/workflows:deepen-plan   → 8 parallel research agents add Enhancement Summary (~18 decisions)
/workflows:review        → 8 parallel review agents read the PLAN as if it were code
                            (find split-brain contradictions, security gaps, schema drift,
                            missing test strategies, undemonstrated promises like `...` bodies)
[triage findings]        → 5 P1, 7 P2, 3 P3 → one todo file per finding, priority-tagged
[fix in plan]            → P1+P2 applied as plan edits; P3 tracked for deferral
/workflows:work          → implement a plan that has already passed review
```

This is the workflow batch 2 used. The plan file is at `docs/plans/2026-04-16-003-feat-pdf-url-ats-analytics-plan.md` — see the "Post-Review Fixes Applied (2026-04-16)" section listing all 12 P1+P2 resolutions.

### The 12 P1+P2 issues caught (by class)

| Todo | Class of bug |
|---|---|
| 013 | Copy-paste/schema drift across plan code blocks (kwarg mismatch, promised error code never emitted, schema field missing) |
| 014 | Security — incomplete threat coverage (IPv4-only SSRF guard, gzip bomb, DNS rebinding TOCTOU) |
| 015 | Undemonstrated security promise (`markdown_to_html` body was `...`) + trivially bypassable defense (static delimiter for prompt injection) |
| 016 | Concurrency / state-machine gap (three-phase intake conflated into one; no dedup in batch ingest) |
| 017 | Stale-state coupling across features (`--overwrite` left dangling refs to regenerated content) |
| 018 | Agent-consumption contract gap (CLI error envelope undefined; error codes not frozen) |
| 019 | Multi-agent deepening split-brain (Interaction Graph, TypedDict promise, constant naming, test names) |
| 020 | Type-precision / data shape contract (`build_aggregator` returned `list[dict]` instead of TypedDict) |
| 021 | Misleading invariant naming ("atomic" two-phase write wasn't actually atomic) |
| 022 | Premature specification (fingerprint migration not needed day 1) — deferred |
| 023 | Cross-batch convention drift (`--content-record PATH` vs `--content-id`) |
| 024 | Exception-hierarchy inconsistency (new errors didn't inherit `ValueError` like batch 1's `ValidationError`) |

Three of those (018, 019, 021) are specifically the split-brain class that the prior-batch postmortem warned about. They were caught this time because reviewers were explicitly asked to look for them.

### Before/after for the three most impactful fixes

**SSRF hardening — todo 014**

Before (IPv4-only, DNS-rebindable, decompression unbounded):

```python
def _validate_url_for_fetch(url):
    parsed = urllib.parse.urlsplit(url)
    ip = socket.gethostbyname(parsed.hostname)   # IPv4 only
    if ipaddress.ip_address(ip).is_private: ...
    return parsed.hostname
# later:
raw = urllib.request.urlopen(url).read()
if encoding == "gzip":
    raw = gzip.decompress(raw)                   # no size cap
```

After (IPv6 covered, all returned addrs validated, streaming cap):

```python
infos = socket.getaddrinfo(parsed.hostname, None)
for family, _t, _p, _c, sockaddr in infos:
    ip = ipaddress.ip_address(sockaddr[0])
    if ip.is_private or ip.is_loopback or ip.is_link_local \
       or ip.is_reserved or ip.is_multicast:
        raise IngestionError(..., error_code="private_ip_blocked")

MAX_DECOMPRESSED_BYTES = 5_000_000
decoder = gzip.GzipFile(fileobj=io.BytesIO(raw))
total = 0; chunks = []
while chunk := decoder.read(65536):
    total += len(chunk)
    if total > MAX_DECOMPRESSED_BYTES:
        raise IngestionError(..., error_code="decompression_bomb")
    chunks.append(chunk)
```

**`markdown_to_html` body — todo 015**

Before (promise-only; body was `...`):

```python
def markdown_to_html(md_text: str) -> str:
    """Escapes all text via html.escape()..."""
    ...          # literal ellipsis in the plan
```

After (escape-first, then markup; negative tests lock the invariant):

```python
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

def _render_inline(text: str) -> str:
    escaped = html.escape(text, quote=True)           # ESCAPE FIRST
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)  # markup AFTER

# Negative tests enforce the invariant:
assert "<script>" not in markdown_to_html("# <script>alert(1)</script>")
assert "&lt;script&gt;" in markdown_to_html("# <script>alert(1)</script>")
assert "<img" not in markdown_to_html("![x](http://evil/x.png)")
```

Plus the prompt-injection delimiter gained a per-ingestion `secrets.token_hex(8)` nonce so a malicious posting can't emit a literal close-tag to break out of the "data-not-instructions" wrapper.

**Inverted `generation → ats_check` coupling — todo 019**

Before (generation.py imports ats_check):

```python
# src/job_hunt/generation.py
from .ats_check import run_ats_check              # coupling
def generate_resume_variants(...):
    variants = _produce(...)
    for v in variants:
        v["ats_check"] = run_ats_check(v)         # hardcoded side-effect
    return variants
```

After (CLI orchestrates; generation is pure; architecture invariant is a testable deliverable):

```python
# src/job_hunt/generation.py  -- no import of ats_check
def generate_resume_variants(...):
    return _produce(...)                           # pure

# src/job_hunt/core.py  (CLI dispatch)
def cmd_generate_resume(args):
    variants = generate_resume_variants(...)
    if not args.no_ats_check:
        for v in variants:
            v["ats_check"] = ats_check.run_ats_check_with_recovery(v)
    write_outputs(variants)

# Testable invariant:
def test_generation_does_not_import_ats_check():
    src = Path("src/job_hunt/generation.py").read_text()
    assert "from .ats_check" not in src
```

### Quantified outcome

- **15 findings surfaced** (5 P1 / 7 P2 / 3 P3); **12 P1+P2 fixed in the plan** before any code written.
- `/workflows:work` implemented the revised plan in **7 commits** (`4a94448` schemas → `7eded78` docs/E2E), all merged cleanly to `main`.
- **156 tests pass on the first implementation pass** (up from 50 in batch 1 baseline; batch 2 added 106 new tests).
- **Zero post-merge bugfix commits.** Every commit in `main~8..main` is a forward-feature or docs commit; no `fix:` follow-ups.
- The three bug classes the workflow catches are precisely the ones implementation-time review misses: promise-only code bodies (`...`), cross-block schema drift, and Enhancement-Summary-vs-body split-brain.

The leverage: an extra `/workflows:review` pass costs ~8 parallel agents of wall time and produces ~15 todo files. It buys the equivalent of 12 P1+P2 bugfix cycles that never have to happen.

## Why This Worked

- **Reviewers were told to treat the plan as code.** Each of the 8 review agents received prompts framing the plan as the review target — not a future code diff. Findings came back as plan edits, not code suggestions.
- **The prior solution was explicitly consulted.** The `learnings-researcher` agent was pointed at `docs/solutions/workflow-issues/reconcile-plan-after-multi-agent-deepening-review.md` and asked "what split-brain patterns from prior batches apply here?" That's compounding knowledge in action — the prior postmortem became a checklist for the next plan.
- **Parallel reviewers triangulated.** Todo 013 (runtime bugs) was independently flagged by 4 reviewers (architecture, python, security, pattern). Single-reviewer blind spots are real; 8 in parallel catches more.
- **Triage happened before implementation.** Each finding was either applied as a plan edit (12 P1+P2), explicitly deferred with rationale (3 P3), or discussed and rejected. No findings silently lost.

## Prevention

### Running `/workflows:review` against a plan

Deploy these agents in parallel, with prompts framed for plan review (not code review):

- **`architecture-reviewer`** — "Read this plan end-to-end. Flag contradictions between sections, module-boundary violations, conflicting data shapes across steps."
- **`security-sentinel`** — "Review this PLAN for security gaps. Flag secrets handling, auth boundaries, input validation, PII leaks. Cite plan sections."
- **`kieran-python-reviewer`** (or language equivalent) — "Check code blocks for syntax/signature consistency and undemonstrated promises like `...` bodies."
- **`pattern-recognition-specialist`** — "Flag naming drifts, convention breaks vs prior batches, and repeated abstractions."
- **`agent-native-reviewer`** — "Is the CLI/tool contract sufficient for agent consumption? Are error codes enumerable and frozen?"
- **`learnings-researcher`** — "Compare this plan against prior solutions in docs/solutions/. Flag any pattern the prior docs warn against."
- **`code-simplicity-reviewer`** — "What's premature or over-specified? Candidates for deferral?"
- **`data-integrity-guardian`** — "Trace every data flow. Flag dangling references, orphaned state, stale-coupling risks."

**Key framing:** tell each agent explicitly "this is a plan, not a PR — findings should be plan edits, not code diffs."

### Split-brain risk checklist (run review if 2+ boxes check)

- [ ] Same entity/type defined in 2+ sections with different fields
- [ ] Two phases touch the same module without a shared interface section
- [ ] "TBD" or "we'll decide later" appears in any load-bearing spot
- [ ] Error handling described differently in happy-path vs failure sections
- [ ] Data flows across module boundaries without a named contract
- [ ] Security/auth described only in one section (should appear at every trust boundary)
- [ ] Plan was deepened by sub-agents without a reconciliation pass

### Fix-in-plan vs defer-to-implementation

| Fix in plan when... | Defer to implementation when... |
|---|---|
| Contradiction between sections (ambiguity compounds during impl) | Variable naming / local style |
| Security gap (retrofitting auth is expensive) | Detail fully contained in one function |
| Data shape / interface decision (ripples across files) | Optimization that requires measurement first |
| Missing test strategy for a risky code path | Finding depends on seeing actual code shape |
| Architecture concern (module boundaries, dependency direction) | |

Rule of thumb: if the fix would span >1 file or change a contract, fix the plan.

### Anti-patterns to avoid

- **Skipping deepen** — a shallow plan can't be meaningfully reviewed; review finds obvious stuff and misses structural issues.
- **Skipping review** — "plan looks good to me" is not a review; you can't see your own blind spots.
- **P1-only bias** — triaging only "bugs" misses P2 architecture/contract issues that cause the next rework cycle.
- **Applying every finding** — reviewers over-generate; contested findings deserve discussion, not auto-apply.
- **Reviewing code you just wrote instead of the plan that produced it** — too late, 10x more expensive to fix.
- **Single reviewer** — one agent has one blind spot; parallel reviewers triangulate.

### Signals for "apply immediately" vs "debate"

**Apply immediately** when a finding has:

- Concrete citation (section X contradicts section Y)
- Security finding with a named attack vector
- Two reviewers independently flag the same issue
- A specific plan-edit suggestion

**Debate / don't auto-apply** when a finding is:

- Hedged ("consider...", "might want to...")
- Stylistic preference with no failure mode cited
- Based on a constraint the plan explicitly rejects
- Out-of-scope noise

### Before-starting-work checklist

1. Plan exists and is deepened (not a one-shot outline)
2. `/workflows:review` run against plan with 2+ parallel agents
3. Findings triaged: applied, deferred, or rejected with reason
4. Plan re-read end-to-end after edits (reconcile pass)
5. Test strategy named per phase
6. Security/auth addressed at every trust boundary
7. No "TBD" in load-bearing sections

### "Not ready to implement" signals

- Plan hasn't been reviewed by anyone but the author
- Any split-brain checklist item unresolved
- Reviewer findings exist but weren't triaged
- Interfaces between phases not explicitly specified
- Author can't answer "what's the failure mode of step N?"

### Measuring whether the process is working

**Leading indicators** (per batch):

- Ratio of plan-edit commits to code-rework commits (target: >3:1)
- Number of review findings caught pre-implementation

**Lagging indicators** (post-merge, 2-week window):

- Zero post-merge bugfix commits referencing the batch
- Implementation tests pass on first run (no rework PRs)
- No architecture-level follow-up tasks spawned
- Time from "start implementing" to "merged" decreases batch-over-batch

If batch N+1 still produces post-merge bugfixes, the review step didn't cover the right surface — rotate reviewer agents or sharpen prompts.

## References

### Prior-art solution

- [`reconcile-plan-after-multi-agent-deepening-review.md`](./reconcile-plan-after-multi-agent-deepening-review.md) — the reactive postmortem this doc proactively builds on. Cross-linked; the two should be read together.

### Batch 2 artifacts

- `docs/plans/2026-04-16-003-feat-pdf-url-ats-analytics-plan.md` — the plan that was reviewed before implementation; see the "Post-Review Fixes Applied (2026-04-16)" section listing all 12 P1+P2 resolutions.
- `todos/013-024-complete-*` — the 12 resolved P1+P2 findings, one file each.
- `todos/025-027-pending-*` — the 3 deferred P3 items with explicit rationale.
- `git log main~8..main` — 7 batch-2 implementation commits, zero bugfix commits.

### Adjacent workflow solutions

- [`bootstrap-agent-first-job-hunt-repo.md`](./bootstrap-agent-first-job-hunt-repo.md) — establishes repo/workflow conventions that plan reviews reference.
- [`extend-cli-with-new-modules-without-breaking-backward-compat.md`](./extend-cli-with-new-modules-without-breaking-backward-compat.md) — pattern reinforced by batch 2 findings on CLI output contract / error codes.
- [`harden-profile-normalization-signal-selection.md`](./harden-profile-normalization-signal-selection.md) — adjacent "catch issues upstream" theme.

### Security pattern

- [`../security-issues/design-secret-handling-as-a-runtime-boundary.md`](../security-issues/design-secret-handling-as-a-runtime-boundary.md) — pattern surfaced during batch 2 pre-implementation review (SSRF, PII handling, credential boundary).

### Governance target

- `AGENTS.md` (Batch 2 Commands section) — codifies the CLI output contract, frozen error-code enums, state machines, and I/O-boundary-vs-internal-logic convention. Many of these were shaped by the 15 review findings rather than discovered during implementation.
