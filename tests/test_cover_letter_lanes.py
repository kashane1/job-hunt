"""Tests for the lane-aware cover-letter pipeline.

Grouped by behavior per plan §"Suggested Test Class Layout":
- CoverLetterLaneSelectionTest
- CoverLetterEvidenceSelectionTest
- CoverLetterRenderingTest
- CoverLetterGuardrailTest
"""
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

from job_hunt.generation import (
    COVER_LETTER_LANE_AI_ENGINEER,
    COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
    COVER_LETTER_LANE_PRIORITY,
    COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER,
    COVER_LETTER_LANE_SPECS,
    COVER_LETTER_MIN_LANE_MARGIN,
    COVER_LETTER_MIN_LANE_SCORE,
    NEEDS_USER_REVIEW_NAME,
    CoverLetterLaneSpec,
    _CoverLetterError,
    _resolve_candidate_name,
    _score_all_lanes,
    _unsafe_prose_reason,
    approved_claims_as_highlights,
    choose_cover_letter_lane,
    find_stale_company_mentions,
    find_unresolved_placeholders,
    generate_cover_letter,
    render_cover_letter_markdown,
    select_cover_letter_evidence,
)
from job_hunt.schema_checks import validate


def _platform_lead() -> dict:
    return {
        "lead_id": "acme-platform-abc",
        "company": "Acme",
        "title": "Staff Platform Engineer",
        "normalized_requirements": {
            "required": ["python", "aws", "platform"],
            "preferred": ["kubernetes"],
            "keywords": ["python", "aws", "platform", "backend", "infrastructure",
                         "internal tools", "data migration"],
        },
    }


def _ai_lead() -> dict:
    return {
        "lead_id": "acme-ai-abc",
        "company": "Acme",
        "title": "AI Engineer",
        "normalized_requirements": {
            "required": ["llm", "rag", "ml"],
            "preferred": ["python"],
            "keywords": ["llm", "rag", "embeddings", "agents", "ai", "human-in-the-loop",
                         "retrieval augmented"],
        },
    }


def _product_lead() -> dict:
    return {
        "lead_id": "acme-product-abc",
        "company": "Acme",
        "title": "Product-Minded Software Engineer",
        "normalized_requirements": {
            "required": ["product", "user"],
            "preferred": ["workflow"],
            "keywords": ["product", "user", "workflow", "customer", "impact",
                         "user empathy", "internal tools"],
        },
    }


def _sample_profile(overrides: dict | None = None) -> dict:
    base = {
        "documents": [
            {"document_id": "job-hunt", "document_type": "project_note",
             "path": "profile/raw/job-hunt.md", "title": "Job Hunt Project"},
            {"document_id": "ai-company-os", "document_type": "project_note",
             "path": "profile/raw/ai-company-os.md", "title": "AI Company OS"},
            {"document_id": "resume", "document_type": "resume",
             "path": "profile/raw/resume.txt", "title": "Resume"},
        ],
        "skills": [
            {"name": "Python", "source_document_ids": ["resume"]},
            {"name": "AWS", "source_document_ids": ["resume"]},
            {"name": "Platform", "source_document_ids": ["resume"]},
            {"name": "Backend", "source_document_ids": ["resume"]},
        ],
        "experience_highlights": [
            {"summary": "Led data migration from MySQL to PostgreSQL preserving integrity across internal tools",
             "source_document_ids": ["resume"]},
            {"summary": "Built internal API integrations for operational tooling supporting millions of events",
             "source_document_ids": ["resume"]},
            {"summary": "Designed system for platform engineering team reducing deploy time by 80%",
             "source_document_ids": ["resume"]},
        ],
        "question_bank": [
            {"question": "Tell me about a project you're proud of.",
             "answer": "I rebuilt a legacy ticketing system with a clearer data model.",
             "provenance": "grounded",
             "source_document_ids": ["resume"]},
            {"question": "Why this company?",
             "answer": "I love their mission and culture.",
             "provenance": "grounded",
             "source_document_ids": ["resume"]},
        ],
        "preferences": {
            "candidate_name": "Kashane Sakhakorn",
            "remote_preference": "remote",
        },
    }
    if overrides:
        base.update(overrides)
    return base


class CoverLetterLaneSelectionTest(unittest.TestCase):
    def test_platform_lead_picks_platform_lane(self) -> None:
        lane_id, source, rationale, warnings = choose_cover_letter_lane(
            _platform_lead(), _sample_profile(),
        )
        self.assertEqual(lane_id, COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)
        self.assertEqual(source, "auto")
        self.assertIn("auto-selected", rationale)

    def test_ai_lead_picks_ai_lane(self) -> None:
        lane_id, _, _, _ = choose_cover_letter_lane(_ai_lead(), _sample_profile())
        self.assertEqual(lane_id, COVER_LETTER_LANE_AI_ENGINEER)

    def test_product_lead_picks_product_lane(self) -> None:
        lane_id, _, _, _ = choose_cover_letter_lane(_product_lead(), _sample_profile())
        self.assertEqual(lane_id, COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER)

    def test_explicit_lane_honored(self) -> None:
        # AI lead but user explicitly wants platform lane.
        lane_id, source, rationale, warnings = choose_cover_letter_lane(
            _ai_lead(), _sample_profile(),
            explicit_lane=COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
        )
        self.assertEqual(lane_id, COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)
        self.assertEqual(source, "explicit")
        self.assertIn("explicit override", rationale)
        # Mismatch between explicit and auto should be recorded.
        codes = [w["code"] for w in warnings]
        self.assertIn("lane_low_confidence", codes)

    def test_invalid_explicit_lane_raises(self) -> None:
        with self.assertRaises(_CoverLetterError) as ctx:
            choose_cover_letter_lane(
                _platform_lead(), _sample_profile(), explicit_lane="nonsense",
            )
        self.assertEqual(ctx.exception.code, "invalid_lane_id")

    def test_tiebreaker_respects_priority(self) -> None:
        # Craft a lead where every lane has equal score (empty keywords).
        lead = {
            "lead_id": "x", "company": "A", "title": "",
            "normalized_requirements": {"required": [], "preferred": [], "keywords": []},
        }
        lane_id, _, _, _ = choose_cover_letter_lane(lead, _sample_profile())
        # With no signal, tiebreaker picks platform (first in priority).
        self.assertEqual(lane_id, COVER_LETTER_LANE_PRIORITY[0])
        self.assertEqual(lane_id, COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)

    def test_low_confidence_warning_when_weak_signal(self) -> None:
        # Minimal signal → low score → low_confidence warning.
        lead = {
            "lead_id": "x", "company": "A", "title": "Engineer",
            "normalized_requirements": {"required": [], "preferred": [],
                                        "keywords": ["engineer"]},
        }
        _, _, _, warnings = choose_cover_letter_lane(lead, _sample_profile())
        codes = [w["code"] for w in warnings]
        self.assertIn("lane_low_confidence", codes)

    def test_scoring_formula_shape(self) -> None:
        """Scores are in [0, 1], sum makes sense, and a strong lead scores highest."""
        scores = _score_all_lanes(
            {"python", "aws", "platform", "backend", "infrastructure", "internal", "tools"}
        )
        for score in scores.values():
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)
        winner = max(scores, key=scores.get)
        self.assertEqual(winner, COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)


class CoverLetterEvidenceSelectionTest(unittest.TestCase):
    def test_selects_accomplishments_and_skills(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        evidence, warnings = select_cover_letter_evidence(
            spec, _platform_lead(), _sample_profile(), None,
        )
        self.assertTrue(evidence["top_accomplishments"])
        self.assertTrue(evidence["top_skills"])
        # Platform lane should pick up the job-hunt project note.
        self.assertIn("job-hunt", evidence["project_note_doc_ids"])

    def test_filters_company_specific_question_bank_entries(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        evidence, _ = select_cover_letter_evidence(
            spec, _platform_lead(), _sample_profile(), None,
        )
        # "Why this company" should be dropped; "Tell me about a project" retained.
        questions = [e["question"] for e in evidence["question_bank_entries"]]
        self.assertNotIn("Why this company?", questions)

    def test_collects_grounded_company_facts(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        research = {
            "company_id": "acme",
            "company_name": "Acme",
            "industry": "DevTools",
            "tech_stack": ["Python", "Kubernetes"],
        }
        evidence, _ = select_cover_letter_evidence(
            spec, _platform_lead(), _sample_profile(), research,
        )
        fields = {f["field"] for f in evidence["company_facts_used"]}
        self.assertIn("industry", fields)
        self.assertIn("tech_stack", fields)

    def test_drops_facts_on_company_name_mismatch(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        research = {
            "company_id": "other",
            "company_name": "OtherCorp",  # mismatch with lead.company=Acme
            "industry": "DevTools",
        }
        evidence, warnings = select_cover_letter_evidence(
            spec, _platform_lead(), _sample_profile(), research,
        )
        self.assertEqual(evidence["company_facts_used"], [])
        codes = [w["code"] for w in warnings]
        self.assertIn("lane_low_confidence", codes)

    def test_absent_research_yields_empty_facts(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        evidence, _ = select_cover_letter_evidence(
            spec, _platform_lead(), _sample_profile(), None,
        )
        self.assertEqual(evidence["company_facts_used"], [])

    def test_zero_evidence_raises(self) -> None:
        empty_profile = {
            "documents": [],
            "skills": [],
            "experience_highlights": [],
            "question_bank": [],
            "preferences": {},
        }
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        with self.assertRaises(_CoverLetterError) as ctx:
            select_cover_letter_evidence(spec, _platform_lead(), empty_profile, None)
        self.assertEqual(ctx.exception.code, "zero_grounded_evidence")


class CoverLetterRenderingTest(unittest.TestCase):
    def _base_evidence(self) -> dict:
        return {
            "top_skills": ["Python", "AWS", "Platform"],
            "top_accomplishments": ["Migrated a legacy system to a modern stack"],
            "accomplishment_source_docs": ["resume"],
            "question_bank_entries": [],
            "project_note_doc_ids": ["job-hunt"],
            "company_facts_used": [],
            "matched_skill_count": 3,
            "matched_requirement_count": 2,
        }

    def test_renders_lane_specific_voice(self) -> None:
        spec_platform = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        spec_ai = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_AI_ENGINEER]
        out_platform = render_cover_letter_markdown(
            spec_platform, _platform_lead(), "Kashane", self._base_evidence(), "remote",
        )
        out_ai = render_cover_letter_markdown(
            spec_ai, _platform_lead(), "Kashane", self._base_evidence(), "remote",
        )
        # Core distinctive phrases should appear per lane.
        self.assertIn("backend engineering", out_platform.lower())
        self.assertIn("ai", out_ai.lower())
        # Letters must differ meaningfully.
        self.assertNotEqual(out_platform, out_ai)

    def test_role_fallback_when_no_company_facts(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        ev = self._base_evidence()
        ev["company_facts_used"] = []
        out = render_cover_letter_markdown(spec, _platform_lead(), "Kashane", ev, "remote")
        # No unsourced "mission" / "vision" / "culture" language when research is absent.
        lower = out.lower()
        self.assertNotIn("mission", lower)
        self.assertNotIn("your product", lower)

    def test_uses_company_facts_when_present(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        ev = self._base_evidence()
        ev["company_facts_used"] = [
            {"source": "company_research", "field": "industry", "value": "DevTools"},
            {"source": "company_research", "field": "tech_stack", "value": "Python, Kubernetes"},
        ]
        out = render_cover_letter_markdown(spec, _platform_lead(), "Kashane", ev, "remote")
        self.assertIn("DevTools", out)
        self.assertIn("Python, Kubernetes", out)

    def test_starts_with_salutation(self) -> None:
        spec = COVER_LETTER_LANE_SPECS[COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS]
        out = render_cover_letter_markdown(
            spec, _platform_lead(), "Kashane", self._base_evidence(), "remote",
        )
        self.assertTrue(out.startswith("Dear Hiring Manager,"))


class CoverLetterGuardrailTest(unittest.TestCase):
    def test_find_unresolved_placeholders(self) -> None:
        self.assertEqual(find_unresolved_placeholders("clean letter"), [])
        hits = find_unresolved_placeholders("I'd love to work at [Company] as a [Role].")
        self.assertEqual(len(hits), 2)

    def test_find_stale_company_mentions_basic(self) -> None:
        text = "I previously worked at SpaceX on launch systems."
        hits = find_stale_company_mentions(text, target_company="Acme")
        self.assertEqual(hits, ["SpaceX"])

    def test_find_stale_company_mentions_escape_hatch(self) -> None:
        text = "I'm excited to apply to SpaceX for this role."
        # When target IS SpaceX, don't flag SpaceX mentions.
        self.assertEqual(find_stale_company_mentions(text, target_company="SpaceX"), [])

    def test_find_stale_company_mentions_word_boundary(self) -> None:
        # "kadincement" (fake word) should not match Kadince thanks to \b.
        text = "kadincement is a made up word"
        self.assertEqual(find_stale_company_mentions(text, target_company="Acme"), [])

    def test_find_stale_company_mentions_case_insensitive(self) -> None:
        text = "kadince handled our prior integration"
        self.assertEqual(find_stale_company_mentions(text, target_company="Acme"), ["Kadince"])


class CoverLetterEndToEndTest(unittest.TestCase):
    def test_generate_produces_valid_record_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(tmpdir),
            )
            self.assertEqual(result["content_type"], "cover_letter")
            self.assertEqual(result["variant_style"], COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)
            self.assertEqual(result["lane_id"], COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)
            self.assertEqual(result["lane_source"], "auto")
            self.assertTrue(Path(result["output_path"]).exists())

            schema = json.loads(
                (ROOT / "schemas" / "generated-content.schema.json").read_text()
            )
            validate(result, schema)

    def test_different_lanes_produce_different_letters(self) -> None:
        # Separate tmpdirs per call — content_id collides when timestamps land in
        # the same second, which is normal in a fast test.
        with tempfile.TemporaryDirectory() as dir_a, \
             tempfile.TemporaryDirectory() as dir_b, \
             tempfile.TemporaryDirectory() as dir_c:
            platform = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(dir_a),
                lane=COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
            )
            ai = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(dir_b),
                lane=COVER_LETTER_LANE_AI_ENGINEER,
            )
            product = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(dir_c),
                lane=COVER_LETTER_LANE_PRODUCT_MINDED_ENGINEER,
            )
            p_md = Path(platform["output_path"]).read_text()
            a_md = Path(ai["output_path"]).read_text()
            pr_md = Path(product["output_path"]).read_text()
            self.assertNotEqual(p_md, a_md)
            self.assertNotEqual(a_md, pr_md)
            self.assertNotEqual(p_md, pr_md)

    def test_explicit_lane_override_records_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # AI lead, but explicitly request platform lane.
            result = generate_cover_letter(
                _ai_lead(), _sample_profile(), None, Path(tmpdir),
                lane=COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
            )
            self.assertEqual(result["lane_source"], "explicit")
            self.assertEqual(result["lane_id"], COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)
            codes = [w["code"] for w in result.get("generation_warnings", [])]
            self.assertIn("lane_low_confidence", codes)  # records the mismatch

    def test_missing_lead_title_hard_fails(self) -> None:
        lead = dict(_platform_lead())
        lead["title"] = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(_CoverLetterError) as ctx:
                generate_cover_letter(lead, _sample_profile(), None, Path(tmpdir))
            self.assertEqual(ctx.exception.code, "missing_lead_field")

    def test_missing_company_hard_fails(self) -> None:
        lead = dict(_platform_lead())
        lead["company"] = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(_CoverLetterError) as ctx:
                generate_cover_letter(lead, _sample_profile(), None, Path(tmpdir))
            self.assertEqual(ctx.exception.code, "missing_lead_field")

    def test_stale_name_in_input_is_filtered_and_warned(self) -> None:
        profile = _sample_profile()
        # Highlight needs lead-relevant keywords so the scorer keeps it; the stale
        # name SpaceX rides along and must be filtered before rendering.
        profile["experience_highlights"].insert(0, {
            "summary": "Led python aws platform data migration at SpaceX across internal tools",
            "source_document_ids": ["resume"],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), profile, None, Path(tmpdir),
            )
            codes = [w["code"] for w in result.get("generation_warnings", [])]
            self.assertIn("stale_name_filtered", codes)
            # Rendered letter must not contain the stale name.
            md = Path(result["output_path"]).read_text()
            self.assertNotIn("SpaceX", md)

    def test_company_facts_used_only_when_research_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # No research: field should be absent entirely.
            res1 = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(tmpdir),
            )
            self.assertNotIn("company_facts_used", res1)

            # Research present with usable facts: list is populated.
            research = {"company_name": "Acme", "industry": "DevTools",
                        "tech_stack": ["Python"]}
            res2 = generate_cover_letter(
                _platform_lead(), _sample_profile(), research, Path(tmpdir),
            )
            self.assertIn("company_facts_used", res2)
            self.assertTrue(res2["company_facts_used"])

            # Research present but wrong company: field is present with [] (distinguishes
            # from "not provided" which omits the field).
            bad_research = {"company_name": "OtherCorp", "industry": "DevTools"}
            res3 = generate_cover_letter(
                _platform_lead(), _sample_profile(), bad_research, Path(tmpdir),
            )
            self.assertIn("company_facts_used", res3)
            self.assertEqual(res3["company_facts_used"], [])


def _sanitized_claims_bank() -> dict:
    """Sanitized, fictional claims bank for claims-mode tests. No private data."""
    return {
        "schema_version": 1,
        "claims": [
            {
                "claim_id": "fixture-migration",
                "claim_text": (
                    "Led a database migration, validating data integrity across the "
                    "full dataset with row-count and constraint checks."
                ),
                "technologies": ["postgres", "sql", "migration"],
                "allowed_lanes": ["platform_backend", "generalist_swe"],
                "review_status": "approved",
            },
            {
                "claim_id": "fixture-api",
                "claim_text": (
                    "Built backend API integrations on a high-volume transactional platform."
                ),
                "technologies": ["api", "backend"],
                "allowed_lanes": ["platform_backend", "generalist_swe"],
                "review_status": "approved",
            },
            {
                "claim_id": "fixture-ai-only",
                "claim_text": "Shipped an internal LLM tool that drafts and routes structured documents.",
                "technologies": ["llm", "python"],
                "allowed_lanes": ["ai_engineer"],
                "review_status": "approved",
            },
            {
                "claim_id": "fixture-needs-review",
                "claim_text": (
                    "Single-handedly delivered a platform with zero data loss and 100% data integrity."
                ),
                "technologies": ["postgres"],
                "allowed_lanes": ["platform_backend"],
                "review_status": "needs_user_review",
            },
        ],
        "never_claim": [
            {"text": "Sole ownership of multi-person achievements.", "reason": "dishonest"},
        ],
    }


class CoverLetterClaimsSafetyTest(unittest.TestCase):
    """Package: cover-letter prose is constrained to approved claims + safe identity."""

    def _profile_no_name(self) -> dict:
        prof = _sample_profile()
        prof["preferences"] = {"remote_preference": "remote"}  # no candidate_name
        prof.pop("contact", None)
        return prof

    # --- approved_claims_as_highlights ---

    def test_only_approved_claims_used(self) -> None:
        highlights = approved_claims_as_highlights(
            _sanitized_claims_bank(), COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS,
        )
        ids = {h["source_document_ids"][0] for h in highlights}
        self.assertIn("claim:fixture-migration", ids)
        self.assertIn("claim:fixture-api", ids)
        # needs_user_review claim must be excluded.
        self.assertNotIn("claim:fixture-needs-review", ids)
        # ai-only claim is out of lane for the platform lane.
        self.assertNotIn("claim:fixture-ai-only", ids)

    def test_vercel_style_backend_selects_platform_claims(self) -> None:
        # A backend lead routes to the platform lane and pulls platform-lane claims.
        lane_id, _, _, _ = choose_cover_letter_lane(_platform_lead(), _sample_profile())
        self.assertEqual(lane_id, COVER_LETTER_LANE_PLATFORM_INTERNAL_TOOLS)
        spec = COVER_LETTER_LANE_SPECS[lane_id]
        evidence, _ = select_cover_letter_evidence(
            spec, _platform_lead(), _sample_profile(), None,
            claims_bank=_sanitized_claims_bank(),
        )
        used = " ".join(evidence["accomplishment_source_docs"])
        self.assertIn("claim:fixture", used)
        self.assertNotIn("fixture-ai-only", used)

    # --- needs_user_review / never_claim / softened phrases excluded from output ---

    def test_needs_user_review_claim_never_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(tmpdir),
                claims_bank=_sanitized_claims_bank(),
            )
            md = Path(result["output_path"]).read_text()
            # The needs_user_review claim carries softened phrases; none may leak.
            self.assertNotIn("zero data loss", md.lower())
            self.assertNotIn("100% data integrity", md.lower())
            self.assertNotIn("single-handedly", md.lower())

    def test_softened_phrase_in_raw_profile_is_filtered(self) -> None:
        # No-bank mode: an unsafe experience highlight must not reach the letter.
        profile = _sample_profile()
        profile["experience_highlights"].insert(0, {
            "summary": (
                "Led python aws platform migration ensuring 100% data integrity "
                "and zero data loss across internal tools"
            ),
            "source_document_ids": ["resume"],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), profile, None, Path(tmpdir),
            )
            md = Path(result["output_path"]).read_text().lower()
            self.assertNotIn("zero data loss", md)
            self.assertNotIn("100% data integrity", md)
            codes = [w["code"] for w in result.get("generation_warnings", [])]
            self.assertIn("unsafe_prose_filtered", codes)

    def test_raw_heading_not_copied(self) -> None:
        # The real bug: a raw cover-letter heading rode in as an "accomplishment".
        profile = _sample_profile()
        profile["experience_highlights"].insert(0, {
            "summary": "# Cover Letter: Software Engineer, Data at Airtable",
            "source_document_ids": ["resume"],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), profile, None, Path(tmpdir),
            )
            md = Path(result["output_path"]).read_text()
            self.assertNotIn("Cover Letter:", md)
            self.assertNotIn("Airtable", md)

    def test_old_cover_letter_title_in_question_bank_dropped(self) -> None:
        profile = _sample_profile()
        profile["question_bank"].insert(0, {
            "question": "Tell me about your work.",
            "answer": "# Cover Letter: Backend Engineer at OtherCo",
            "provenance": "grounded",
            "source_document_ids": ["resume"],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), profile, None, Path(tmpdir),
            )
            md = Path(result["output_path"]).read_text()
            self.assertNotIn("Cover Letter:", md)

    # --- signature / identity ---

    def test_missing_name_does_not_become_candidate(self) -> None:
        self.assertEqual(_resolve_candidate_name(self._profile_no_name()), NEEDS_USER_REVIEW_NAME)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), self._profile_no_name(), None, Path(tmpdir),
                claims_bank=_sanitized_claims_bank(),
            )
            md = Path(result["output_path"]).read_text()
            self.assertNotIn("\nCandidate\n", md)
            self.assertIn(NEEDS_USER_REVIEW_NAME, md)
            codes = [w["code"] for w in result.get("generation_warnings", [])]
            self.assertIn("name_needs_review", codes)

    def test_explicit_candidate_name_preserved(self) -> None:
        prof = _sample_profile()
        prof["preferences"]["candidate_name"] = "Real Person"
        self.assertEqual(_resolve_candidate_name(prof), "Real Person")

    # --- approved claims appear; conservative fallback when none ---

    def test_approved_claim_appears_in_conservative_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(tmpdir),
                claims_bank=_sanitized_claims_bank(),
            )
            md = Path(result["output_path"]).read_text().lower()
            # A fragment of an approved claim should be present.
            self.assertTrue(
                "validating data integrity" in md or "api integrations" in md,
                msg=f"expected approved-claim prose in letter:\n{md}",
            )

    def test_insufficient_approved_claims_flags_not_invents(self) -> None:
        empty_bank = {"schema_version": 1, "claims": [], "never_claim": []}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(tmpdir),
                claims_bank=empty_bank,
            )
            md = Path(result["output_path"]).read_text()
            # Letter is produced (conservative), with a review flag, and no
            # fabricated specifics from the raw profile.
            self.assertTrue(Path(result["output_path"]).exists())
            codes = [w["code"] for w in result.get("generation_warnings", [])]
            self.assertIn("no_approved_claims", codes)
            # The raw profile's metric-bearing highlight must not appear.
            self.assertNotIn("80%", md)

    def test_generation_without_private_data_uses_fixtures(self) -> None:
        # Sanitized fixtures only: claims-mode end-to-end succeeds, schema-valid.
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(
                _platform_lead(), _sample_profile(), None, Path(tmpdir),
                claims_bank=_sanitized_claims_bank(),
            )
            schema = json.loads(
                (ROOT / "schemas" / "generated-content.schema.json").read_text()
            )
            validate(result, schema)
            self.assertTrue(Path(result["output_path"]).exists())

    # --- unit: unsafe-prose classifier ---

    def test_unsafe_prose_reason_classifies(self) -> None:
        deny = ("zero data loss",)
        self.assertIsNone(_unsafe_prose_reason("Built a clean backend service.", deny))
        self.assertEqual(_unsafe_prose_reason("", deny), "empty")
        self.assertEqual(
            _unsafe_prose_reason("# Cover Letter: X", deny), "raw_markdown_heading",
        )
        self.assertEqual(
            _unsafe_prose_reason("achieved zero data loss", deny),
            "denylisted_phrase:zero data loss",
        )
        self.assertEqual(
            _unsafe_prose_reason("[Fill in your achievements]", deny),
            "template_placeholder",
        )


if __name__ == "__main__":
    unittest.main()
