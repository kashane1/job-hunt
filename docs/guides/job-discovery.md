# Job Discovery Guide

Active job discovery turns hours of manual URL collection into a single
command. `python3 scripts/job_hunt.py discover-jobs` reads
`config/watchlist.yaml`, polls every configured source, dedupes against
existing leads, filters by keyword/location/seniority, and writes new leads
via the same canonical path as `ingest-url`.

## Setup

1. Copy the template:

   ```bash
   cp config/watchlist.example.yaml config/watchlist.yaml
   ```

   `config/watchlist.yaml` is **gitignored** because it contains
   user-specific target-company names (PII-adjacent). The `.example.yaml`
   template stays tracked.

2. Add companies. Each entry needs at least ONE of `greenhouse`, `lever`,
   or `careers_url`:

   ```yaml
   companies:
     - name: "ExampleCo"
       greenhouse: "exampleco"
       lever: "exampleco"
       careers_url: "https://exampleco.com/careers"
   ```

3. Validate before running:

   ```bash
   python3 scripts/job_hunt.py watchlist-validate
   ```

## Filter semantics

All four filter lists are optional; empty lists mean "no constraint". When
non-empty, the rules compose as follows:

- `keywords_none` (highest precedence) — any substring hit excludes.
- `keywords_any` — at least one must match `title + location`.
- `locations_any` — at least one must match `location`.
- `seniority_any` — at least one must match `title`.

Matching is case-insensitive substring. Unicode is casefolded before
comparison. Precedence order: `keywords_none > keywords_any > locations_any > seniority_any`.

### Worked examples

**Example 1** — "Senior Backend Engineer, Remote - US"
with `keywords_any: [engineer]`, `locations_any: [remote]`,
`seniority_any: [senior, staff]`: **passes** all four filters.

**Example 2** — "Senior Backend Engineer (TS/SCI required)"
with `keywords_none: [ts/sci]`: **excluded** regardless of other matches.

**Example 3** — "Product Manager, Remote"
with `keywords_any: [engineer, developer]`: **excluded** (no title match).

## Cursor behavior

`data/discovery/state.json` tracks `(company, source)` tuples that have
completed at least one successful run. Subsequent runs skip already-seen
listings. Cursor advances only when a source run is:

- Complete (no errors mid-traversal)
- Not budget-capped (`--max-ingest` didn't cut it short)
- Not listing-truncated (response body fit under `MAX_LISTING_BYTES`)

Reset with:

```bash
python3 scripts/job_hunt.py discover-jobs --reset-cursor "ExampleCo|greenhouse"
python3 scripts/job_hunt.py discover-jobs --reset-cursor "ExampleCo|*"
```

## Review-queue triage

Generic career crawls that produce only one confidence signal land in
`data/discovery/review/<entry_id>.md`. List, promote, or dismiss them:

```bash
python3 scripts/job_hunt.py review-list
python3 scripts/job_hunt.py review-promote <entry_id>
python3 scripts/job_hunt.py review-dismiss <entry_id> --reason "off topic"
```

`review-promote` re-validates the stored URL through the same SSRF guard
`ingest-url` uses, so a stored loopback URL is blocked at promotion.

## LinkedIn and Indeed policy

Both hard-fail at every entry point (listing, careers crawl, promote).
They're behind login walls, aggressively bot-detected, and legally risky
to scrape. If a careers URL points at either site it's rejected during
watchlist validation. Manual ingestion via
`python3 scripts/job_hunt.py extract-lead --input <file>` is the escape
hatch.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `error_code: watchlist_invalid` | YAML parse error or schema miss | Run `watchlist-validate` for specifics. |
| `error_code: cursor_corrupt` | `state.json` failed schema validation | `rm data/discovery/state.json` and re-run. |
| `error_code: anti_bot_blocked` | Careers page is gated by Cloudflare/Akamai | Add a Greenhouse/Lever slug if available; otherwise skip. |
| `error_code: watchlist_comments_present` | `watchlist-add` would lose comments | Pass `--force` or edit the YAML directly. |
| 50-company runs are slow | Cold robots cache / LLM scoring inline | First cold run takes 8–12 min; subsequent ~3–6 min. |

Clear the robots cache with:

```bash
python3 scripts/job_hunt.py robots-cache-clear
```

## Config-tracking convention deviation

`config/watchlist.yaml` is the first config file that is **gitignored**.
All prior configs (`scoring.yaml`, `runtime.yaml`, `sources.yaml`,
`skills-taxonomy.yaml`) are tracked. Watchlist content is PII-adjacent
(your target-company list), so it stays local.

Future configs with similar sensitivity should follow the same pattern:

- Gitignore the real file.
- Track a `.example.yaml` template with safe placeholder content.
- Document the deviation in `AGENTS.md`.
