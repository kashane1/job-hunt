# Application Answering Prompt

When preparing answers:
- prefer grounded facts from `profile/normalized/`
- label synthesized answers clearly
- do not invent unsupported facts by default
- record missing facts that need review
- choose assets that best match the role

## Hard rule: no temporal or tenure inflation

Time-based claims are the most easily falsified and the most damaging in a
job application (a reviewer runs `git log` in 30 seconds). Never state or
imply a duration, tenure, or production-longevity claim unless the exact
figure is grounded in `profile/normalized/`.

- Banned unless explicitly grounded: "over the last year", "for months",
  "running in production for months", "years of", "long-running", or any
  phrasing that asserts how long something has existed or run.
- For ai-company-os specifically, the only sanctioned framing is the one in
  `profile/raw/ai-company-os.md`: built intensively over roughly two months,
  ~565 commits, already ships real products and runs recurring workflows
  behind approval gates with an audit trail. Do not embellish beyond this.
- Prefer checkable specifics (commit counts, named files, shipped apps) over
  vague impressiveness. A true, scrutiny-proof claim beats a stronger-sounding
  one that fails inspection.

