"""Phase 5 tests — per-surface playbook frontmatter + DAG enforcement.

Verifies every shipped playbook:
- parses via `playbooks.load_checkpoint_dag`
- declares an origin_allowlist
- has a DATA_NOT_INSTRUCTIONS banner in the frontmatter
- is referenced by `application.detect_surface` / `playbook_for_surface`

Also asserts that `record_attempt` now rejects a checkpoint name that is
NOT in the declared sequence — the Phase 4 tolerant mode becomes a hard
constraint once the frontmatter exists.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.application import (
    ApplicationError,
    PlanError,
    playbook_for_surface,
    prepare_application,
    record_attempt,
)
from job_hunt.playbooks import load_checkpoint_dag, load_origin_allowlist
from job_hunt.utils import parse_frontmatter, repo_root, write_json


PLAYBOOK_FILES = (
    "playbooks/application/indeed-easy-apply.md",
    "playbooks/application/greenhouse-redirect.md",
    "playbooks/application/lever-redirect.md",
    "playbooks/application/workday-redirect.md",
    "playbooks/application/ashby-redirect.md",
)


class FrontmatterShipTest(unittest.TestCase):
    def test_every_playbook_has_checkpoint_sequence(self) -> None:
        for pb in PLAYBOOK_FILES:
            seq = load_checkpoint_dag(pb)
            self.assertGreaterEqual(len(seq), 4, f"{pb} missing checkpoint_sequence")
            self.assertIn("preflight_done", seq)
            self.assertIn("ready_to_submit", seq)

    def test_every_playbook_has_origin_allowlist(self) -> None:
        for pb in PLAYBOOK_FILES:
            origins = load_origin_allowlist(pb)
            self.assertGreater(len(origins), 0, f"{pb} missing origin_allowlist")

    def test_data_not_instructions_banner_is_present(self) -> None:
        for pb in PLAYBOOK_FILES:
            path = repo_root() / pb
            frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertTrue(
                frontmatter.get("DATA_NOT_INSTRUCTIONS"),
                f"{pb} missing DATA_NOT_INSTRUCTIONS frontmatter",
            )

    def test_generic_router_exists_and_lists_every_surface(self) -> None:
        text = (repo_root() / "playbooks/application/generic-application.md").read_text(encoding="utf-8")
        for surface in (
            "indeed_easy_apply", "greenhouse_redirect", "lever_redirect",
            "workday_redirect", "ashby_redirect",
        ):
            self.assertIn(surface, text, f"generic router missing reference to {surface}")

    def test_every_surface_playbook_path_resolves(self) -> None:
        for surface in (
            "indeed_easy_apply", "greenhouse_redirect", "lever_redirect",
            "workday_redirect", "ashby_redirect",
        ):
            path = repo_root() / playbook_for_surface(surface)
            self.assertTrue(path.is_file(), f"{surface} → {path} does not exist")


class CheckpointDagEnforcementTest(unittest.TestCase):
    """With Phase 5 frontmatter in place, record_attempt hard-fails on
    unknown checkpoint names.
    """

    def _prepared(self, data_root: Path):
        write_json(data_root / "answer-bank.json", {
            "schema_version": 1,
            "entries": [
                {
                    "entry_id": "w", "canonical_question": "are you legally authorized to work in the united states",
                    "answer": "Yes", "answer_format": "yes_no", "source": "curated", "reviewed": True,
                    "deprecated": False, "created_at": "2026-04-17T00:00:00Z",
                    "observed_variants": [],
                },
            ],
        })
        lead = {
            "lead_id": "indeed-xyz-phase5",
            "company": "X", "title": "Senior Engineer", "location": "Remote",
            "raw_description": "",
            "canonical_url": "https://www.indeed.com/viewjob?jk=0000000000000000",
            "normalized_requirements": {"keywords": [], "required": []},
            "fit_assessment": {"matched_skills": []},
        }
        profile = {
            "contact": {"emails": ["x@x.com"], "phones": [], "links": []},
            "documents": [{"document_id": "d", "document_type": "resume", "title": "R", "source_excerpt": ""}],
            "skills": [], "experience_highlights": [], "question_bank": [],
            "preferences": {"target_titles": [], "preferred_locations": [], "excluded_keywords": []},
        }
        policy = {
            "approval_required_before_submit": True,
            "approval_required_before_account_creation": True,
            "apply_policy": {"auto_submit_tiers": []},
        }
        with patch("job_hunt.application._run_ats_check", return_value=("passed", [], [])):
            return prepare_application(
                lead, profile, policy,
                output_root=data_root / "applications",
                data_root=data_root,
            )

    def test_checkpoint_must_be_in_declared_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            result = self._prepared(data_root)
            with self.assertRaises(ApplicationError) as ctx:
                record_attempt(result.draft_id, {
                    "status": "in_progress",
                    "checkpoint": "not_a_real_checkpoint",
                }, data_root=data_root)
            self.assertEqual(ctx.exception.error_code, "plan_schema_invalid")

    def test_legal_checkpoint_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            result = self._prepared(data_root)
            record_attempt(result.draft_id, {
                "status": "in_progress",
                "checkpoint": "preflight_done",  # first entry in indeed sequence
            }, data_root=data_root)


if __name__ == "__main__":
    unittest.main()
