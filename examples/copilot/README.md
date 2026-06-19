# Co-pilot example artifacts

Sample outputs from the Level 1.5 application co-pilot, produced by a real
dry-run against five public Anthropic Greenhouse postings on 2026-06-18. No
final submit was performed (the co-pilot cannot submit). The files contain only
public posting metadata and generic fit text — no personal data.

| File | Produced by |
|------|-------------|
| `recent-scan.example.json`     | `scan-recent-jobs --since 1h` |
| `resume-selection.example.json`| `select-resume-variant --lead <ai-engineer-lead>` |
| `decision-log.example.json`    | `copilot-run --since 1h --min-tier no` |
| `decision-log.example.md`      | same run, human-readable |

See `docs/ai/architecture-copilot-level-1.5.md` for the design and the full
command chain.
