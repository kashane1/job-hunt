# Claims truth bank

A private-aware store of approved, evidence-backed accomplishment claims plus an
explicit never-claim list. Resume/cover-letter tailoring may only draw on claims
marked `review_status: approved`, and only for the lanes listed in each claim's
`allowed_lanes`. **Nothing here may be fabricated.**

## Files

| File | Tracked? | Purpose |
|------|----------|---------|
| `claims-bank.example.json` | yes (sanitized) | Fictional structure demo — safe to commit |
| `claims-bank.json`         | **no (gitignored)** | Your real claims — never committed |
| schema | `schemas/claims-bank.schema.json` | Validate with `verify-artifact` |

## Setup

```bash
cp profile/claims/claims-bank.example.json profile/claims/claims-bank.json
# edit profile/claims/claims-bank.json with YOUR true, sourced claims
python3 scripts/job_hunt.py verify-artifact \
  --schema schemas/claims-bank.schema.json \
  --artifact profile/claims/claims-bank.json
python3 scripts/job_hunt.py profile-doctor
```

## Rules (see also AGENTS.md "Core Policies")

- Every claim must be **literally true** and have an `evidence` source.
- Leave `impact` **null** rather than inventing a metric.
- A claim is usable only when `review_status: approved`.
- `allowed_lanes` gates which resume variants may surface a claim.
- Keep rejected over-claims with `review_status: rejected` so they are not
  reintroduced. Add patterns to `never_claim` with a reason.
