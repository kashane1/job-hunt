---
status: complete
priority: p1
issue_id: "068"
tags: [code-review, security, triage, spoofing]
dependencies: []
---

# Triage trusts attacker-supplied Authentication-Results (forgeable DKIM-d= binding)

## Problem Statement

`confirmation.parse_email{,_dict}` reads the `Authentication-Results`
header **verbatim from the inbound message**. `triage.correlate_recruiter`
→ `dkim_pass_domain` then binds trust to that header. Nothing between the
wire and the trust decision asserts the header was stamped by a *trusted
verifier* (Gmail's inbound MTA). A raw `.eml` supplied via
`--inbox-file` is fully author-controlled.

Concrete bypass (verified by security-sentinel):

```
From: "Stripe Recruiting" <careers@evil.attacker.net>
Authentication-Results: dkim=pass header.d=stripe.com
Subject: Update on your Stripe application — phone screen
```

`triage_inbox`'s anti-spoof gate only quarantines `_OUTCOME_LABELS`
(`rejection`/`offer`). A forged **`phone_screen`/`interview`** therefore
**auto-advances Model B** for a `stripe.com` lead → poisons
`calibrate-scoring`. This is exactly the "forgeable end-to-end by an
unauthenticated remote attacker" failure the deepened plan claimed to
eliminate.

## Findings

- `triage.py` `_DKIM_D_RE` `.search` takes the FIRST `dkim=pass …
  header.d=`; an attacker appends a fake pass segment after a real
  `dkim=fail`. No anchoring, no iterate-all-results.
- DMARC / `From` alignment is not enforced despite the plan's Invariant 1.
- `_MULTI_SUFFIXES` is missing common ccTLD eTLDs (`co.il`, `com.tr`,
  `co.kr`, `org.nz`, …) → `attacker.co.il` collapses to `co.il`.
- Anti-spoof gate covers only `rejection`/`offer`; `phone_screen`/
  `interview`/`assessment_request` from a non-allowlisted sender are not
  gated.

## Proposed Solutions

### Option 1 (Recommended): non-allowlisted senders never auto-advance; DKIM-d= is advisory
- Any classified label from a sender **not in `SENDER_ALLOWLIST`** →
  quarantine for human promotion, regardless of DKIM-d= match. DKIM-d=
  equality only *raises* the quarantine's confidence annotation; it never
  authorizes a silent Model-B write.
- Harden `_DKIM_D_RE`: iterate every `dkim=` result; fail-closed if any
  result for the candidate domain is not `pass`; require `header.d=`
  registrable == stored company domain AND `From` registrable aligned.
- Document that raw-`.eml` `--inbox-file` is trusted-operator input; the
  Gmail-API path relies on Gmail's inbound verification.
- Pros: closes the bypass; matches the human-submit/propose-not-auto
  posture already in AGENTS.md. Cons: recruiter outcomes need a
  promotion path (todo 070). Effort: Small. Risk: Low.

### Option 2: wire a trusted verifier (SPF+DKIM+DMARC re-check in-process)
- Re-verify signatures locally instead of trusting the header.
- Pros: enables non-allowlisted auto-advance safely. Cons: large, needs
  DNS/DKIM crypto, out of stdlib posture. Effort: Large. Risk: Medium.

## Recommended Action

Option 1 (blocks merge).

## Acceptance Criteria

- [ ] Non-allowlisted sender + any stage-changing label → quarantine, zero Model-B writes (test with forged `phone_screen`/`interview`).
- [ ] `_DKIM_D_RE` iterates all `dkim=` results, fail-closed on any non-pass for the candidate domain.
- [ ] `From` registrable-domain alignment enforced alongside `header.d=`.
- [ ] AGENTS.md + guide state the raw-`.eml` trust boundary.
- [ ] Full suite green.

## Work Log

- 2026-05-18: Found by security-sentinel during PR #4 review.

## Resources

- PR: https://github.com/kashane1/job-hunt/pull/4
- `src/job_hunt/triage.py` (`dkim_pass_domain`, `_DKIM_D_RE`, `triage_inbox`, `_OUTCOME_LABELS`)
- `src/job_hunt/confirmation.py` (`parse_email`, `_quarantine`)
