---
title: "Indeed surface detection via schema.org JobPosting.directApply"
category: integration-issues
tags: [indeed, schema-org, json-ld, apply-batch, surface-detection]
module: job_hunt.application + ingestion + core
symptom: "apply-batch prepared drafts for indeed.com URLs that turned out to redirect to an external company ATS; the configured Easy Apply playbook could not drive them so 4 of 5 attempted drafts were wasted work"
root_cause: "detect_surface() classified postings by URL pattern alone. Every indeed.com/viewjob?jk=... URL was labeled indeed_easy_apply, but Indeed's viewjob page hosts two different apply experiences (Apply with Indeed vs. Apply on company site) that need different playbooks"
severity: high
date: 2026-04-19
---

# Indeed surface detection via schema.org JobPosting.directApply

## Problem

On 2026-04-19, `python3 scripts/job_hunt.py apply-batch --top 10 --source indeed`
prepared drafts for the 10 highest-scoring Indeed postings. The agent
then attempted to drive the Indeed Easy Apply playbook against each. Of
the 5 drafts it tried, 4 redirected to external company ATSes (Veeva,
Linktree, Kohl's, Wasserman). Only MGA Entertainment was actually Easy
Apply. Each wasted draft had already cost: plan.json generation, a
tailored resume, an ATS-compatibility check, and a per-lead directory.

The posting URLs were indistinguishable. All five lived at
`indeed.com/viewjob?jk=<id>`. The difference was what Indeed rendered on
the page: "Apply with Indeed" (direct) versus "Apply on company site"
(external redirect). Our URL-pattern classifier had no way to see that.

## Why URL-only detection felt correct

`detect_surface` was introduced in Phase 4 with a regex table:

```python
_SURFACE_URL_MATCHERS = (
    (re.compile(r"^https?://(www\.)?indeed\.com/viewjob", re.IGNORECASE), "indeed_easy_apply"),
    (re.compile(r"^https?://boards\.greenhouse\.io/", re.IGNORECASE), "greenhouse_redirect"),
    ...
)
```

Greenhouse, Lever, Ashby, and Workday URLs ARE identity-as-URL: if the
URL matches `boards.greenhouse.io`, the posting is on Greenhouse. Indeed
looked like it fit the same shape. It doesn't. Indeed's viewjob page is a
shell that hosts two apply experiences depending on the employer's
configuration, and the URL is identical in both cases.

Three options that were considered and rejected:

1. **HEAD request the apply button's destination.** Requires driving the
   browser or fetching rendered HTML at `detect_surface` time. The
   classifier is called synchronously from `prepare_application` and has
   no browser context. Moving browser I/O into classification inverts
   the layering.
2. **Keyword-match "Easy Apply" in the posting description.** The
   description text doesn't reliably mention the apply mechanism. False
   positives and false negatives both common.
3. **Accept the waste, let the agent bail on external postings.** Each
   wasted draft is a plan.json + a tailored resume + an ATS check. At 40%
   external-redirect rate that's 4 drafts of wasted compute per 10-posting
   sweep plus the cognitive cost of reviewing the "why did this skip"
   attempts.json entries.

## Solution

Indeed's viewjob page already carries the authoritative signal: a
schema.org `JobPosting` JSON-LD blob with a `directApply: true|false`
boolean. The ingestion path already parses that blob (landed in
`73bab46`, Phase 4 of discovery-hardening). We extend it to pull one
more field, thread it through to the lead record, and branch on it in
the surface classifier.

**Step 1: ingestion extracts the field.**
`_fetch_indeed_viewjob` in `src/job_hunt/ingestion.py` reads
`directApply` from the JSON-LD node and falls back to DOM button-label
matching when the field is absent:

```python
direct_apply = node.get("directApply")
if isinstance(direct_apply, bool):
    apply_type = "direct" if direct_apply else "external"
...
# DOM-button fallback when JSON-LD didn't carry directApply.
if apply_type is None:
    lower = html_text.lower()
    if "apply on company site" in lower:
        apply_type = "external"
    elif "apply with indeed" in lower or "easy apply" in lower:
        apply_type = "direct"
```

The field is emitted into the markdown frontmatter alongside the
existing ingestion fields.

**Step 2: lead extraction propagates the field.**
`extract_lead` in `src/job_hunt/core.py` reads `apply_type` from the
frontmatter and stores it on the lead record:

```python
apply_type_val = metadata.get("apply_type")
if apply_type_val:
    lead["apply_type"] = str(apply_type_val)
```

**Step 3: surface detection branches on it.**
`detect_surface` in `src/job_hunt/application.py` grows an optional
`apply_type` parameter. Indeed URLs with `apply_type == "external"` map
to a new surface `"indeed_external_redirect"`; everything else keeps
the old behavior:

```python
def detect_surface(posting_url: str, apply_type: str | None = None) -> str:
    for pattern, surface in _SURFACE_URL_MATCHERS:
        if pattern.search(posting_url):
            if surface == "indeed_easy_apply" and apply_type == "external":
                return "indeed_external_redirect"
            return surface
    return "indeed_easy_apply"
```

**Step 4: apply-batch filters external out of the top-N.**
`_select_leads` skips external-redirect postings when `--source indeed`:

```python
if source.startswith("indeed") and lead.get("apply_type") == "external":
    continue
```

Legacy leads without the field (`apply_type == ""`) fall through and
still get prepared, preserving backward compatibility while the old
records age out.

### Measured impact

Before fix: 10-draft batch contained 4 external-redirect wastes and
6 direct. After fix: follow-up 10-draft batch was 10/10 direct,
10/10 tier_1.

## Why this is the right shape

- **The signal is authoritative, not heuristic.** `directApply` is
  Indeed's own structured declaration of which apply path the posting
  uses. It's not a keyword match or a screen-scrape of button text; the
  DOM fallback exists only for the rare pages where JSON-LD is missing.
- **Three-layer propagation matches the existing data flow.** Ingestion
  writes frontmatter, core reads frontmatter into lead records,
  application reads the lead record. Adding a field to that pipe costs
  three small edits and no new infrastructure.
- **Tolerant of old data.** Leads ingested before this change have
  `apply_type == ""`. `_select_leads` treats unknown as "not skippable"
  rather than "definitely external," so old records don't silently
  disappear from batch selection.
- **Reuses the existing playbook mapping.** `indeed_external_redirect`
  points to the same playbook file as `indeed_easy_apply` today because
  we don't have a distinct external-redirect playbook yet. The surface
  enum is there for the agent to branch on later without another schema
  change.

## Prevention

URL-as-identity is a convenient classifier, but it fails whenever one
host renders multiple experiences. Ask before trusting a URL pattern:

1. **Does the host render exactly one apply mechanism?** Greenhouse,
   Lever, Ashby, and Workday: yes. Indeed, LinkedIn, ZipRecruiter, and
   Google Jobs: no. If the host is an aggregator or a viewjob shell,
   the URL identifies the page, not the apply path.
2. **Is there a structured signal inside the page?** Check for
   `schema.org` JSON-LD first. Indeed, LinkedIn, and most modern job
   boards emit `JobPosting` blobs with `directApply`, `hiringOrganization`,
   and `url` fields that are more reliable than DOM scraping.
3. **If the classifier guesses wrong, what does it cost?** A
   misclassification that costs one wasted HTTP call is tolerable. A
   misclassification that costs a tailored resume, an ATS check, and an
   agent attempt is not: surface that as a hard branch with a new
   surface enum, not a runtime fallback.
4. **When adding a new ingestion field, default the consumer to tolerant.**
   The `apply_type == ""` fallthrough in `_select_leads` is the
   tolerant-consumer pattern applied to schema evolution: old data keeps
   working, new data gets the new treatment, and a future
   `check-integrity` extension can promote to strict once legacy records
   have aged out.

## Related

- [ship-tolerant-consumers-before-strict-producers.md](../workflow-issues/ship-tolerant-consumers-before-strict-producers.md)
  the three-layer propagation here is the same tolerant-reader pattern:
  ingestion adds the field, consumers branch on it when present, old
  records pass through untouched.
- `73bab46 feat(discovery-hardening): Phase 4 — Indeed viewjob JSON-LD ingestion`
  added the JSON-LD extractor that we now read `directApply` from.
- `e23b52d fix(apply-batch): skip re-discovered already-submitted leads`
  carries the `detect_surface` signature change and the `_select_leads`
  filter alongside its headline fix.
- Code: `src/job_hunt/ingestion.py` (`_fetch_indeed_viewjob`),
  `src/job_hunt/core.py` (`extract_lead`),
  `src/job_hunt/application.py` (`detect_surface`, `_select_leads`).
