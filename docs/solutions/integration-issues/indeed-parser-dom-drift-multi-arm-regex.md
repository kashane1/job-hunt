---
title: "Tolerate Indeed search-card DOM drift with a multi-arm regex anchored on the accessibility tree"
category: integration-issues
tags: [scraping, dom-drift, indeed, regex, accessibility-tree, tolerant-parser]
module: job_hunt.indeed_discovery
symptom: "`discover-jobs` returns `entry_count: 0` for every `indeed_search` source despite HTTP 200 responses, ~1.7 MB bodies, and no anti-bot trigger."
root_cause: "`_TITLE_RE` encoded a single legacy card shape (`<h2 class=\"jobTitle\">`). Indeed's 2026 redesign moved titles into `<span id=\"jobTitle-{jk}\">` inside a `<a data-jk=… class=\"jcs-JobTitle\">`, so the regex never matched and `parse_search_results` skipped every card via the `if not title: continue` guard."
severity: high
date: 2026-04-18
---

# Tolerate Indeed search-card DOM drift with a multi-arm regex anchored on the accessibility tree

## Problem

`python3 scripts/job_hunt.py discover-jobs` returned `entry_count: 0` for every `indeed_search` source on 2026-04-18. Every symptom pointed to a healthy pipeline:

- Fetches returned HTTP 200 with ~1.7 MB bodies.
- `detect_anti_bot` did not fire; no Cloudflare/Akamai challenge.
- `_JK_DATA_ATTR_RE` found 16 `data-jk` attributes per page. the cards were there.
- `parse_search_results` still returned `[]`, because `_TITLE_RE` matched nothing in the 4 KB window after each `data-jk=` and the parser skipped every posting via `if not title: continue`.

The old `_TITLE_RE` was scoped to Indeed's pre-2025 card shape:

```html
<h2 class="jobTitle ..."><span ...>Title</span></h2>
```

The live 2026 shape had moved to:

```html
<a data-jk="{jk}" ... aria-label="full details of {title}" class="jcs-JobTitle ...">
  <span title="{title}" id="jobTitle-{jk}">{title}</span>
</a>
```

No `<h2 class="jobTitle">` anywhere. The parser worked exactly as written; the markup underneath it had shifted.

## Why the failure was invisible

Nothing raised. The resolution ladder in `parse_search_results` has two passes:

1. JSON-LD `@type = JobPosting`. Indeed's server-rendered React no longer ships these on search pages, so the first pass yields zero hits silently.
2. Heuristic `data-jk` scan. every card was found, every title was empty, every card was skipped via the `if not title: continue` line at `src/job_hunt/indeed_discovery.py:263`.

The `continue` was load-bearing for the old shape (some card variants genuinely lack a title and should be dropped), so removing it is not safe. Observably the pipeline was doing its job:

- Fetch layer: OK.
- Anti-bot layer: OK.
- Card-detection layer: 16 cards found.
- Title-extraction layer: 0 titles extracted, silently.
- Discovery output: `entry_count: 0`.

There is no log line between "16 cards found" and "0 entries returned" because the skip is an expected code path. The only tell was the combination HTTP 200 + non-empty `data-jk` count + zero parsed entries, which is exactly the signature of a title-regex drift.

## Solution

Replace the single-shape title regex with a three-arm alternation that anchors on the accessibility tree first, then falls back to the current CSS-class shape, then to the legacy tag shape. Alternation groups yield `None` for the non-matching arms, so the reader picks whichever group fired.

```python
# src/job_hunt/indeed_discovery.py:52
_TITLE_RE: Final = re.compile(
    r'<span[^>]*id="jobTitle-[a-f0-9]{16}"[^>]*>(.*?)</span>'
    r'|aria-label="(?:full details of |)([^"]+?)"[^>]*class="[^"]*jcs-JobTitle'
    r'|<(?:h2|h3)[^>]*(?:class="jobTitle[^"]*"|data-testid="jobtitle")[^>]*>(.*?)</(?:h2|h3)>',
    re.IGNORECASE | re.DOTALL,
)
```

Arm-by-arm:

1. **Current (2026) shape**. `<span id="jobTitle-{jk}">…</span>`. Uses the `jk`-scoped `id` so it cannot collide with unrelated spans.
2. **Accessibility-tree fallback**. `aria-label="full details of {title}"` on the anchor that carries `class="… jcs-JobTitle …"`. The `aria-label` text is the most stable signal Indeed ships, because it is regression-tested against the accessibility tree. The `(?:full details of |)` tolerates the prefix being dropped in future variants.
3. **Legacy (pre-2025) shape**. `<h2 class="jobTitle …">` or `<h2 data-testid="jobtitle">`. Kept so partial rollouts (A/B cohorts seeing the old DOM) keep working.

The reader in `parse_search_results` iterates the capture groups and takes the first non-empty one:

```python
# src/job_hunt/indeed_discovery.py:254
title_match = _TITLE_RE.search(window)
...
title = ""
if title_match:
    title_raw = next((g for g in title_match.groups() if g), "")
    title = _strip_tags(title_raw)
```

After the fix, the live fetch parsed 16 of 16 postings on the same page that previously returned 0. Committed in `8a9c79c`, which also adds `employer_name` to `ListingEntry` so the real employer (for example "Veeva Systems") survives past the point where `discovery.py` overwrites `source_company` with the watchlist's virtual-company name.

## Prevention

1. **Prefer accessibility-tree anchors over CSS classes.** `aria-label`, `role`, and `id` values tied to stable identifiers (here, `jobTitle-{jk}`) survive redesigns because Indeed regression-tests them against its a11y suite. CSS class names (`jobTitle`, `jcs-JobTitle`) are cosmetic and get reshuffled with each design pass.
2. **Keep old arms when adding new ones.** When Indeed ships the next redesign, add a fourth alternation branch; do not delete the existing three. Partial rollouts routinely serve mixed DOMs to different users or cohorts for weeks, and a parser that only understands the newest shape will silently drop half the results.
3. **Order arms by expected freshness.** Current shape first (fastest match on the common case), accessibility-tree fallback in the middle (stable long-term anchor), legacy shape last (rare, but drops to zero cost when absent).
4. **Add the HTTP-200-plus-zero-entries signature to the monitoring surface.** `data-jk` count and parsed-entry count diverging is a deterministic signal of title-regex drift and should be easy to spot from a single discovery run's telemetry, rather than requiring a post-hoc body dump.
5. **Tolerant comment at the regex.** The existing docstring at `_JK_DATA_ATTR_RE` already notes "the page markup has changed every year and will again." The new `_TITLE_RE` comment (lines 47-51) carries the same warning and names the three shapes explicitly, so the next editor does not assume alternation arms are dead code.

## Related

- `src/job_hunt/indeed_discovery.py`. `_TITLE_RE` (line 52) and `parse_search_results` heuristic pass (line 247).
- Commit `8a9c79c feat(discovery-hardening): Phase 3 — Indeed 2026 card shape + employer_name`.
- [indeed-surface-detection-via-directapply.md](indeed-surface-detection-via-directapply.md). another case of using a stable Indeed-internal signal (the `directapply` attribute) rather than CSS-class heuristics.
- [../workflow-issues/ship-tolerant-consumers-before-strict-producers.md](../workflow-issues/ship-tolerant-consumers-before-strict-producers.md). same family of "tolerant consumer" pattern applied to phased plan rollout rather than external-DOM drift.
