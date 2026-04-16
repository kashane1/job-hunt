---
title: Design secret handling as a runtime boundary for trust-first job application automation
date: 2026-04-15
module: browser_application_runner
problem_type: security_issue
component: secret_handling
symptoms:
  - Plan and runtime supported sign-in and account creation flows, but secret handling was only implied
  - Application reports risked storing raw credentials or session material from browser attempt payloads
  - Approval, reporting, and browser execution rules were stronger than the credential-handling design around them
root_cause: Secret handling was treated as an operational prerequisite instead of a first-class subsystem with explicit policy, storage rules, report redaction, and verification coverage
tags:
  - security
  - secrets
  - redaction
  - browser-automation
  - reporting
  - trust
severity: high
---

# Design secret handling as a runtime boundary for trust-first job application automation

## Problem

The job-hunt system was designed to sign in and potentially create accounts, but the original plan and implementation did not treat credential handling as a designed subsystem. That created a trust gap: browser attempt payloads could include secret-like fields, while reports and JSON artifacts were expected to be durable and git-safe.

## Root Cause

The repository had strong policies around approval, provenance, and browser tab limits, but credential handling lived only as an implicit assumption. Without an explicit runtime boundary, there was no single answer for:

- where credentials should live
- how browser flows should access them
- what fields must be redacted before artifact writes
- how failures should be recorded without leaking secrets

## Solution

Make secret handling an explicit policy and artifact concern instead of an implementation detail.

### What changed

1. Expanded runtime policy to include secret source and artifact-redaction settings.
2. Added a separate approval gate for account creation, since auth-related actions carry different risk than final submit.
3. Redefined application reports to persist redaction state, blocker details, checkpoint records, and richer browser metrics.
4. Added redaction logic in the core pipeline so secret-like fields are replaced before reports are written.
5. Updated schemas, example attempt data, and tests so credential safety is enforced by code and validation, not only by docs.

### Key implementation pattern

```yaml
approval_required_before_submit: true
approval_required_before_account_creation: true
secret_source: env_or_local_untracked_file
redact_secrets_in_artifacts: true
```

```python
def redact_sensitive_data(value, path: str = "") -> tuple[object, list[str]]:
    if isinstance(value, dict):
        redacted = {}
        redacted_paths = []
        for key, item in value.items():
            current_path = f"{path}.{key}" if path else key
            if any(keyword in key.lower() for keyword in SENSITIVE_KEYWORDS):
                redacted[key] = "[REDACTED]"
                redacted_paths.append(current_path)
                continue
            cleaned_item, child_paths = redact_sensitive_data(item, current_path)
            redacted[key] = cleaned_item
            redacted_paths.extend(child_paths)
        return redacted, redacted_paths
    return value, []
```

### Why this worked

- It aligned credential handling with the repo's trust-first goal instead of leaving it as a hidden runtime assumption.
- It made reports safe to persist even when runtime attempt payloads contain auth-related data.
- It turned secret handling into something testable and schema-backed, not just something people are expected to remember.

## Prevention

- If a workflow can sign in, create accounts, or handle session state, define secret storage and redaction rules in the initial architecture.
- Keep secrets in environment variables or ignored local files, never in tracked config or reports.
- Treat auth-related approvals separately when the risk profile differs from the final business action.
- Add at least one end-to-end test that proves secret-like attempt fields are redacted before artifact writes.
- Keep sample data intentionally unsafe enough to exercise redaction paths during development.

## References

- `AGENTS.md`
- `config/runtime.yaml`
- `schemas/application-draft.schema.json`
- `schemas/application-report.schema.json`
- `src/job_hunt/core.py`
- `tests/test_pipeline.py`
- `docs/plans/2026-04-15-001-feat-agent-first-job-hunt-system-plan.md`
