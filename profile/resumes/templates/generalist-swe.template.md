<!--
RESUME LANE AUTHORING TEMPLATE — generalist_swe (DEFAULT / FALLBACK lane)
This is a SCAFFOLD, not a resume. The registry's default_variant routes here when
no specialized lane clears the confidence threshold. The shipped config points
resume_path at the bundled example resume so a fresh checkout always resolves;
replace it by authoring profile/resumes/generalist-swe.md (gitignored) and
updating resume_path in config/resume-variants.json.
Lane is defined in config/resume-variants.json -> variants[id=generalist_swe].
-->

# Lane: Generalist Software Engineer (`generalist_swe`) — DEFAULT

- **review_status:** missing   <!-- missing | draft | ready -->
- **resume file:** currently `examples/profile/raw/resume.md` (bundled example).
  Replace with `profile/resumes/generalist-swe.md` (gitignored) when authored.

## Role as the fallback
This lane catches everything that does not match a specialized lane. Keep it a
broadly strong software-engineering resume, not tuned to any single stack. It is
what unmatched / ambiguous leads will use, so it should always be present and
ATS-clean.

## Target titles
- Software Engineer, Software Developer, Backend Engineer (generic), SWE (any level)

## Target keywords (emphasize)
python, backend, api, sql, aws, automation, testing, code review

## Negative keywords (avoid / disqualifying)
clearance, polygraph, relocation required, onsite-only, sponsorship unavailable

## Summary angle
A balanced summary of your core engineering strengths that reads well for a broad
range of SWE roles. Avoid over-specializing; lead with reliability and range.

## Approved claims (by claim_id, approved + lane-allowed only)
- [ ] <claim_id>  — <one-line gloss>
- [ ] <claim_id>  — <one-line gloss>

## Never-claim for this lane
- No specialization you cannot back (this lane is intentionally generalist).
- See `profile/claims/claims-bank.json` -> `never_claim`.

## Strongest projects / accomplishments
1. <project> — <broadly impressive, stack-agnostic framing>
2. <project> — <...>

## Evidence / source notes
- <claim/project> → <where it is documented>

## Missing facts to fill in
- [ ] <core skills to foreground for a general SWE audience>
- [ ] <quantified impact you can actually source>

## Work authorization / location / salary caveats
- Work authorization: <fill from preferences>
- Location / remote: <fill>
- Compensation floor: <optional; keep out of the resume itself>

## Notes
Keep ATS-friendly: Technical Skills, Professional Experience, Education sections;
≤1 page for <5 YOE. Run `ats-check` after authoring.
