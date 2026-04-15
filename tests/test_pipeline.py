from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.core import (
    build_application_draft,
    extract_lead,
    normalize_profile,
    score_lead,
    summarize_run,
    write_application_report,
)
from job_hunt.schema_checks import validate


class PipelineTest(unittest.TestCase):
    def test_end_to_end_artifacts_are_generated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_raw = root / "profile" / "raw"
            profile_raw.mkdir(parents=True)
            normalized = root / "profile" / "normalized"
            leads_dir = root / "data" / "leads"
            applications_dir = root / "data" / "applications"
            runs_dir = root / "data" / "runs"
            reports_dir = root / "docs" / "reports"

            (profile_raw / "resume.md").write_text(
                """---
document_type: resume
title: Staff Platform Resume
tags:
  - python
  - platform
  - backend
---
# Staff Platform Resume

- Built internal platform automation for deployment and operations
- Led backend work across Python, Postgres, and AWS
Contact: person@example.com
""",
                encoding="utf-8",
            )
            (profile_raw / "qa.md").write_text(
                """---
document_type: question_bank
title: Common Answers
---
Q: Why do you want this role?
A: I like work that blends platform engineering, automation, and product impact.
""",
                encoding="utf-8",
            )
            (profile_raw / "preferences.md").write_text(
                """---
document_type: preferences
title: Preferences
target_titles:
  - Staff Platform Engineer
preferred_locations:
  - Remote
remote_preference: remote
excluded_keywords:
  - clearance
---
""",
                encoding="utf-8",
            )

            profile = normalize_profile(
                root / "profile",
                normalized,
                {"skill_keywords": ["python", "platform", "backend", "aws", "postgres"]},
            )

            lead_source = root / "lead.md"
            lead_source.write_text(
                """---
source: greenhouse
company: ExampleCo
title: Staff Platform Engineer
location: Remote
application_url: https://example.com/jobs/123
---
# Staff Platform Engineer

## Requirements
- Python
- Platform
- AWS
- Postgres
""",
                encoding="utf-8",
            )
            lead = extract_lead(lead_source, leads_dir)
            lead = score_lead(
                lead,
                profile,
                {
                    "title_match_weight": 20,
                    "skills_match_weight": 35,
                    "seniority_match_weight": 10,
                    "location_match_weight": 10,
                    "domain_match_weight": 10,
                    "compensation_match_weight": 5,
                    "negative_keyword_penalty_weight": 10,
                    "strong_yes_threshold": 75,
                    "maybe_threshold": 55,
                    "negative_keywords": ["clearance"],
                },
            )
            draft = build_application_draft(
                lead,
                profile,
                {
                    "approval_required_before_submit": True,
                    "browser_tabs_soft_limit": 10,
                    "browser_tabs_hard_limit": 15,
                    "stop_if_required_fact_missing": True,
                },
                applications_dir,
            )
            draft["approval"]["approved"] = True
            (applications_dir / f"{draft['draft_id']}.json").write_text(
                json.dumps(draft, indent=2), encoding="utf-8"
            )

            report = write_application_report(
                draft,
                {
                    "attempted": True,
                    "confirmed_submitted": True,
                    "account_action": "reused",
                    "blocked_reason": "",
                    "final_url": "https://example.com/thanks",
                    "tab_metrics": {"max_open_tabs": 3},
                },
                {"browser_tabs_soft_limit": 10, "browser_tabs_hard_limit": 15},
                applications_dir,
                reports_dir,
            )
            summary = summarize_run(leads_dir, applications_dir, runs_dir, reports_dir)

            schemas_root = Path(__file__).resolve().parents[1] / "schemas"
            validate(profile, json.loads((schemas_root / "candidate-profile.schema.json").read_text()))
            validate(lead, json.loads((schemas_root / "lead.schema.json").read_text()))
            validate(draft, json.loads((schemas_root / "application-draft.schema.json").read_text()))
            validate(report, json.loads((schemas_root / "application-report.schema.json").read_text()))
            validate(summary, json.loads((schemas_root / "run-summary.schema.json").read_text()))


if __name__ == "__main__":
    unittest.main()
