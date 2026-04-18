# Cover Letter Generation Guide

Cover letters are generated through `src/job_hunt/generation.py:generate_cover_letter`.
The pipeline is lane-aware: one public orchestrator + three pure helpers + two
detection helpers. See `docs/plans/2026-04-18-001-feat-cover-letter-lanes-plan.md`
for the full design contract.

## Lanes

Three strength lanes, each with its own preferred keywords, phrases, voice, and
project-note allowlist:

| Lane id | Emphasis | Voice |
|---------|----------|-------|
| `platform_internal_tools` | backend, platform, migrations, internal tooling, reliability | practical engineering mindset, clear systems, strong data foundations |
| `ai_engineer` | AI systems, LLMs, agents, human-in-the-loop, typed workflows | production engineering judgment + AI systems curiosity |
| `product_minded_engineer` | user empathy, workflow improvements, operational pain | practical impact, building systems people can trust |

Pass `--lane <id>` to `generate-cover-letter` to pick explicitly, or `--lane auto`
(default) to let scoring pick based on lead signal.

## Scoring

Auto-selection uses the same formula as resume variants:

```
lane_score = 0.7 * jaccard(combined_tokens, lead_keywords) + 0.3 * phrase_boost
```

where `combined_tokens = lead.title + normalized_requirements.keywords + required`,
and `phrase_boost` is the fraction of the lane's `preferred_phrases` present in
the keyword bag. Thresholds are `Final` constants in `generation.py`:

- absolute score `>= 0.15`
- top-2 margin `>= 0.05`

Below either threshold → `lane_low_confidence` warning, still proceed with the winner.

## Section structure

1. **Opening** — role + company + lane-specific emphasis.
2. **Proof** — top accomplishment (scored by lead + lane) + matched skills.
3. **Alignment** — grounded company facts OR role-specific fallback.
4. **Optional candidate paragraph** — question-bank entry (generic prompts only).
5. **Closing** — lane value proposition + availability + thank-you.

## Grounding rules

- **Never fabricate** company mission, vision, culture, customers, product, or values.
- Company-specific language may only come from `lead` or `company_research`.
- If `company_research.company_name` disagrees with `lead.company`, drop the research and warn.
- Question-bank entries with company-specific prompts ("why this company?", "our mission") are filtered before evidence selection.
- Raw `profile/raw/cover-letter*.txt` files are not read at generation time; stale-company names like `SpaceX` and `Kadince` are denylisted with a word-boundary matcher and target-escape hatch.

## Guardrails

Pre-write (generation.py hard-fails, aborts before artifact write):

- `unresolved_placeholder` — `[Company]`, `[Role]`, `{Team}`, etc.
- `wrong_company_name` — denylisted name that is not the target.
- `missing_lead_field` — lead lacks title or company.
- `invalid_lane_id` — explicit lane is unknown.
- `zero_grounded_evidence` — no lane had sufficient evidence.

Post-write (ats_check.py, backstop for anything that escapes generation-time):

- Same hard errors as above (re-checked on the rendered artifact).
- Warnings: `weak_evidence_density`, `unsupported_company_language`.

## Output record fields

The cover-letter record carries these optional lane-specific fields in addition to
the resume/answer-set core:

- `lane_id`, `lane_source` (`auto` / `explicit`), `lane_rationale`
- `selected_question_bank_questions`
- `company_facts_used` (absent if no research provided; `[]` if provided but unusable)
- `generation_warnings` (list of `{code, severity, detail}` records)

`variant_style` mirrors the resolved `lane_id` for backward compatibility.

## Tone

- Professional but not stiff.
- Confident but not arrogant.
- Specific to the role, not generic.
- Brief: ~4 paragraphs, target under 400 words.
- If company research is missing, talk concretely about the work itself rather than speculating about the organization.
