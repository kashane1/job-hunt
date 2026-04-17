---
title: Keep humans as the submission actor when automating against services whose ToS prohibits third-party bots
date: 2026-04-17
module: indeed_auto_apply
problem_type: security_issue
component: policy_design
symptoms:
  - Target service (Indeed.com) ToS explicitly prohibits "third-party bots or other automated tools to apply for jobs"
  - Service policy does not distinguish personal automation from mass scraping
  - Account-ban risk was blocking the "autonomous apply" feature from shipping
  - An initial mitigation (user-creates-a-marker-file acknowledging ToS) was weak — it documented risk without reducing it
  - Auto-submit on behalf of the user exposed the tool's worst failure mode (hallucinated answers submitted without review)
root_cause: The initial architecture treated "autonomous" as "agent clicks the Submit button," which put the tool squarely in the category the ToS prohibits. Autonomy was defined by what the agent does END-to-end rather than what it does up to the final action with legal/financial/reputational consequences.
tags:
  - security
  - tos-compliance
  - legal
  - browser-automation
  - approval-gates
  - agent-design
  - human-in-the-loop
severity: high
---

# Keep humans as the submission actor when automating against services whose ToS prohibits third-party bots

## Problem

The `job-hunt` repo added capability to autonomously apply to Indeed.com job postings. The initial plan had the agent drive Chrome via the Claude-in-Chrome MCP through the full flow: navigate → find job → fill form → **click Submit** → capture confirmation. A "tiered by confidence" approval policy auto-submitted applications that met all supported-fact, ATS-check-passed, and no-inferred-answer conditions; everything else paused at Submit for human review.

Then during the `/deepen-plan` pass, the Best-Practices Researcher surfaced the real ToS landscape:

> **Indeed's 2026 Job Seeker Guidelines:** *"Indeed prohibits job seekers from using third-party bots or other automated tools to apply for jobs. Breaking these rules may result in your account being closed."*
>
> Indeed refreshed its ToS in January 2026. Their detection stack uses network reputation, anomaly detection, and ML on top of Cloudflare Managed Challenge with behavioral analysis. Indeed's policy does not distinguish personal automation from mass scraping.

The first attempted mitigation (see resolved todo #044) was a weak fix: require the user to create a `data/.tos-acknowledged` marker file on first run after reading a risk disclosure. This documented that the user was warned but did not reduce the surface. The tool was still a third-party bot submitting applications on the user's behalf. Account bans were still possible; risk severity sat at High.

## Root Cause

"Autonomous" was defined too broadly. The plan equated "agent acts end-to-end" with "agent clicks Submit." But the submission click is the **single action** that:

1. Has the legal distinction under Indeed's ToS (a third-party bot "applying" vs. a form-fill assistant helping a human apply).
2. Has irreversible external consequences (an application sent cannot be unsent; a bad auto-submitted answer has the same blast radius as a good one).
3. Is where the user's judgment is most valuable per unit of time (30 seconds of review vs. 3 minutes of form-filling).

The original plan optimized for a metric ("% of applications auto-submitted") that required crossing the legal boundary. Re-reading Indeed's policy line by line made it clear: when the human is the submitting entity, the tool is categorically different from what the ToS prohibits.

## Solution

**Human-in-the-loop on every submit click. The agent fills forms but never commits them.**

Specifically:

- `apply_policy.auto_submit_tiers = []` is a **compile-time invariant**, not a runtime-configurable tier. AGENTS.md Safety Overrides semantics still hold: runtime overrides can tighten (force field-by-field review) but cannot loosen (enable auto-submit).
- Every per-surface playbook's **Step 6 is the human submit gate**. The agent emits a structured JSON payload describing what it would submit, and waits for the user to click Submit in their own Chrome window.
- **Tiers are re-purposed.** They no longer control whether Submit auto-fires; they control **how much field-level review the human does** before clicking:
  - **tier_1 (streamlined)**: all supported facts, ATS check passed, no inferred answers → user glances at a single-screen summary and clicks Submit.
  - **tier_2 (field-by-field)**: at least one inferred answer, unknown question, or ATS warning → user reviews each flagged field with its provenance before clicking.
- A new attempt state `paused_human_abort` captures the case where the user decides not to submit (the agent times out waiting for URL change and records the non-submission cleanly).
- The `data/.tos-acknowledged` marker file requirement is **removed**. A human clicking Submit is a stronger consent signal than a file.
- The residual risk (automated *filling* can still trigger Indeed's anti-bot heuristics at volume) is addressed separately: log-normal pacing, coffee breaks every 4-6 apps, daily cap ≤20 applications. `docs/guides/indeed-auto-apply.md` documents this residual risk but does not gate on acknowledgment.

## Why This Is Stronger Than the Marker-File Alternative

| Dimension | Marker-file acknowledgment | Human-in-the-loop on submit |
|---|---|---|
| ToS posture | Tool is a third-party bot; user waived objections | Tool is a form-fill assistant; human is the submitting entity |
| Legal distinction | None — submitting bot is still a submitting bot | Meaningful — "automation" stops before the ToS-triggering action |
| Worst-case blast radius | Hallucinated answer auto-submitted | Hallucinated answer surfaced to human, caught at review |
| User friction | One-time marker creation | One click per application (minutes in a 35-min batch) |
| Account-ban risk | High (automated filling + submitting) | Medium (automated filling only; human submits) |

The marker-file path documented awareness. The human-in-the-loop path **changes the category the tool falls under** in the service's policy framework.

## Solution Code

Plan section excerpts that codify the invariant:

**Runtime policy:**

```python
# src/job_hunt/core.py — inside DEFAULT_RUNTIME_POLICY
"apply_policy": {
    "default_tier": "tier_2",
    "auto_submit_tiers": [],             # v4 invariant: human always clicks Submit
    "tier_1_requirements": {             # tier-1 now means "streamlined review", not auto-submit
        "all_answers_supported": True,
        "ats_check_status": "passed",
        "no_account_creation": True,
        "preflight_not_already_applied": True,
    },
    # ... pacing, cap, retention keys ...
},
```

**Per-surface playbook (every one of them — Indeed Easy Apply, Greenhouse, Lever, Workday, Ashby):**

```markdown
## Step 6: Human submit gate (ALL tiers pause here)
Do NOT click submit under any circumstances. Emit structured output:
{
  "ready_to_submit": true,
  "draft_id": "…",
  "tier": "tier_1|tier_2",
  "screenshot_path": "data/applications/{draft_id}/checkpoints/pre_submit.png",
  "field_summary": [{"field_id", "question", "answer", "provenance"}],
  "tier_2_review_items": [ …items the human should double-check… ]
}
The user reviews the form in their Chrome window and clicks Submit themselves.
The agent waits for the URL change / confirmation signal to proceed.

## Step 7: Post-submit confirmation capture (agent resumes after human click)
Poll for URL change or in-page confirmation text (up to 30s).
If 30s elapse without a confirmation signal, write attempt
status=paused_human_abort and exit cleanly.
```

**Attempt state machine extension:**

```
in_progress → {submitted_provisional, submitted_confirmed, paused_tier2,
               paused_unknown_question, paused_human_abort, failed,
               dry_run_only, unknown_outcome}
```

## Prevention / Best Practices

**Principle (reusable across any future ToS-constrained automation):**

> When automating against a service whose Terms prohibit third-party bots performing the action, move the agent's responsibility up to — but not including — the action itself. Let the human remain the actor of record for the commit. The agent's value shifts from "does the action" to "prepares the action to the point of human review," which captures most of the time savings without crossing the policy boundary.

**Applicable surfaces (examples, not exhaustive):**

- Job-board applications (Indeed, LinkedIn Easy Apply, Glassdoor) — apply this pattern.
- Automated email sending on a personal account — draft the email, let the human hit Send (most email clients already enforce this).
- Trading / money movement — already prohibited by the computer-use MCP instructions at our tier-level: *"never execute a trade, place an order, send money, or initiate a transfer on the user's behalf — always ask the user to perform those actions themselves."* This doc generalizes that established pattern to any ToS-restricted commit action.
- Social media posting on a personal account — same pattern: compose, the human posts.
- Account creation / sign-up flows — existing `approval_required_before_account_creation` policy in this repo is the same pattern applied to a narrower action.

**Design checks to run on any new "autonomous X" plan:**

1. Does the target service's ToS prohibit third-party bots/automation for this action? (Check; don't assume.)
2. If yes, which single click/API call is the commit action? That click is off-limits to the agent.
3. Is the remaining pre-commit work meaningfully automatable? (If yes, ship the "agent drafts, human commits" version.)
4. Does the repo's approval-policy system expose this as an invariant (compile-time) rather than a config-gated tier (loosen-able)? Prefer compile-time.
5. Is the residual risk (the agent's *automated non-commit actions* — filling, scraping, etc.) acceptable on its own? If yes, ship. If no, reconsider automating this surface at all.

**Anti-pattern to avoid:**

> "We'll make auto-submit a config option, default off. Power users can flip it on."

This is a loosen-able setting dressed as an invariant. Once auto-submit is in the code path, a user hits the wrong config value or a future PR flips the default, and the ToS boundary is crossed without review. Make it a compile-time invariant that only future code changes (visible in review) can alter — not a runtime toggle.

## Tests / Verification

- Unit test that `apply_policy.auto_submit_tiers` cannot be set to a non-empty list via any runtime-policy merge path (tighten-only semantic verified by test, not just doc).
- Integration test that each per-surface playbook's final pre-submit checkpoint emits the `ready_to_submit: true` structured payload and does **not** make a `mcp__Claude_in_Chrome__click` call against a submit button.
- Regression test asserting `paused_human_abort` is emitted when the confirmation poll times out after a pre-submit checkpoint (not `failed`, not `unknown_outcome`).
- Negative test that a malicious runtime override like `--apply-policy auto_submit_tiers='["tier_1"]'` is rejected with a `PlanError(policy_loosen_attempt)` or equivalent.

## Cross-References

- Plan: [docs/plans/2026-04-16-005-feat-indeed-auto-apply-plan.md](../../plans/2026-04-16-005-feat-indeed-auto-apply-plan.md) — see the "v4 Policy Revision" section at the top and the updated "Approval posture" key decision.
- Brainstorm: [docs/brainstorms/2026-04-16-indeed-auto-apply-brainstorm.md](../../brainstorms/2026-04-16-indeed-auto-apply-brainstorm.md) — original "Tiered by confidence" decision that v4 partially reverses in a principled way.
- Related security-issue solution: [design-secret-handling-as-a-runtime-boundary.md](design-secret-handling-as-a-runtime-boundary.md) — same pattern (policy as a runtime boundary, not an operational assumption) applied to credential handling.
- Related workflow-issue solution: [bootstrap-agent-first-job-hunt-repo.md](../workflow-issues/bootstrap-agent-first-job-hunt-repo.md) — established the original approval-gate-before-submit posture that v4 preserves as an invariant.
- Resolved todo: [todos/044-complete-p1-indeed-tos-risk-acknowledgment.md](../../../todos/044-complete-p1-indeed-tos-risk-acknowledgment.md) — records the decision to supersede the marker-file approach.
- Computer-use MCP instructions (built-in tool-use guidance): *"Never execute a trade, place an order, send money, or initiate a transfer on the user's behalf."* Same pattern, generalized here to ToS-constrained commits.

## What This Compounds

The first time this pattern arose (Indeed), it took a full brainstorm + plan + deepen + technical-review cycle — plus one bad first-attempt fix (marker file) — to arrive at the right architectural invariant. Documenting it here means:

- Future "autonomous X on Y-service" plans in this repo start by asking: *"Does Y's ToS prohibit third-party bots for the target action? If yes, human-in-the-loop-on-commit is the default."*
- LinkedIn Easy Apply (already flagged as a future domain to allowlist) gets this pattern for free; the playbook skeleton, tier semantics, and policy invariant are already proven.
- The reusable mental model — "agent drafts, human commits" — is promoted from an incident-specific fix to a first-class design pattern.
