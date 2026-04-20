from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.boards.base import ApplicationTarget
from job_hunt.surfaces.registry import (
    batch_eligible,
    cover_letter_policy,
    executor_backend_for,
    get_surface_spec,
    handoff_kind_for,
    playbook_for_surface,
    surface_policy_for,
)


class SurfaceRegistryTest(unittest.TestCase):
    def test_linkedin_manual_assist_metadata(self) -> None:
        spec = get_surface_spec("linkedin_easy_apply_assisted")
        self.assertEqual(spec.playbook_path, "playbooks/application/linkedin-easy-apply-assisted.md")
        self.assertEqual(spec.default_executor, "none")
        self.assertEqual(spec.handoff_kind, "manual_assist")
        self.assertEqual(surface_policy_for(spec.surface), "automation_forbidden_on_origin")

    def test_batch_eligibility_is_surface_owned(self) -> None:
        automated = ApplicationTarget(origin_board="indeed", surface="indeed_easy_apply")
        manual = ApplicationTarget(origin_board="linkedin", surface="linkedin_easy_apply_assisted")
        self.assertTrue(batch_eligible(automated.surface, automated))
        self.assertFalse(batch_eligible(manual.surface, manual))

    def test_cover_letter_policy_varies_by_surface(self) -> None:
        self.assertEqual(
            cover_letter_policy("workday_redirect")["preferred_stage"],
            "explicit_documents_step",
        )
        self.assertEqual(
            cover_letter_policy("linkedin_easy_apply_assisted")["preferred_stage"],
            "human_review_step",
        )

    def test_compatibility_helpers_resolve_surface_metadata(self) -> None:
        self.assertTrue(playbook_for_surface("greenhouse_redirect"))
        self.assertEqual(executor_backend_for("greenhouse_redirect"), "claude_chrome")
        self.assertEqual(handoff_kind_for("greenhouse_redirect"), "automation_playbook")


if __name__ == "__main__":
    unittest.main()
