---
title: Record the Glassdoor-hosted automation policy exception for the human-submit lane
date: 2026-04-21
module: planning_and_application_architecture
problem_type: security_issue
component: documentation
tags:
  - glassdoor
  - policy
  - browser-automation
  - human-submit
severity: high
---

# Record the Glassdoor-hosted automation policy exception for the human-submit lane

## Decision

The repository intentionally accepts a **narrow, board-specific policy
exception** for Glassdoor-hosted application automation:

- scope: explicit manual/local Glassdoor intake only
- action boundary: the agent may navigate, open the form, fill supported
  fields, upload prepared assets, and stop at the human submit gate
- hard stop: the agent never clicks the final Submit button
- exclusions: no global `glassdoor.com` allowlist widening for
  ingestion/discovery in this slice, no anti-bot evasion, no automatic
  confirmation-state promotion beyond `submitted_provisional`

## Why this exists

Glassdoor's cited Terms prohibit introducing software or automated agents to
the service without express written permission. The repo's standard
human-submit invariant remains necessary, but it is not sufficient on its own
to explain why this board should be automated.

This document makes the additional policy choice explicit: for this board, the
project still wants the human-submit automation lane despite that stricter
language, and it limits the blast radius accordingly.

## Guardrails

- Keep the first slice on manual/local intake artifacts.
- Fail closed on unknown redirect hosts.
- Reuse shared ATS playbooks when Glassdoor hands off to Greenhouse, Lever,
  Workday, or Ashby.
- Treat login, MFA, CAPTCHA, anti-bot, and unapproved account creation as
  human-only boundaries.
- Abort rather than retry through anti-bot challenges.
- Keep Glassdoor at `submitted_provisional` until verified sender/DKIM/body
  correlation rules land for confirmation mail.

## Fallback

If this exception is later revoked, the fallback path is the conservative
manual-assist-first plan at
`docs/plans/2026-04-20-001-feat-glassdoor-board-support-plan.md`.
