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
    _jaccard,
    generate_answer_set,
    generate_cover_letter,
    generate_resume_variants,
    generation_tokens,
    match_question_to_bank,
    match_question_to_knockout,
    select_accomplishments_for_variant,
    select_skills_for_variant,
)
from job_hunt.schema_checks import validate
from job_hunt.utils import read_json


def _sample_profile() -> dict:
    return {
        "schema_version": "1",
        "generated_at": "2026-04-15T00:00:00+00:00",
        "contact": {
            "emails": ["person@example.com"],
            "phones": ["(555) 123-4567"],
            "links": ["https://linkedin.com/in/person"],
        },
        "documents": [
            {"document_id": "doc-resume", "path": "resume.md", "document_type": "resume",
             "title": "Resume", "source_excerpt": "..."},
            {"document_id": "doc-qa", "path": "qa.md", "document_type": "question_bank",
             "title": "QA", "source_excerpt": "..."},
        ],
        "skills": [
            {"name": "Python", "source_document_ids": ["doc-resume"]},
            {"name": "AWS", "source_document_ids": ["doc-resume"]},
            {"name": "Postgres", "source_document_ids": ["doc-resume"]},
            {"name": "Docker", "source_document_ids": ["doc-resume"]},
            {"name": "React", "source_document_ids": ["doc-resume"]},
            {"name": "Kubernetes", "source_document_ids": ["doc-resume"]},
            {"name": "TypeScript", "source_document_ids": ["doc-resume"]},
        ],
        "experience_highlights": [
            {"summary": "Built system design for distributed data pipeline handling 10M events/day",
             "source_document_ids": ["doc-resume"]},
            {"summary": "Led migration of monolith to microservices architecture reducing deploy time by 80%",
             "source_document_ids": ["doc-resume"]},
            {"summary": "Drove business impact: increased revenue 25% through API integration optimization",
             "source_document_ids": ["doc-resume"]},
            {"summary": "Mentored 5 engineers across frontend and backend teams in cross-functional project",
             "source_document_ids": ["doc-resume"]},
            {"summary": "Designed data model for real-time analytics platform with sub-second latency",
             "source_document_ids": ["doc-resume"]},
            {"summary": "Reduced infrastructure costs by 40% through optimization and auto-scaling",
             "source_document_ids": ["doc-resume"]},
        ],
        "question_bank": [
            {"question": "Why do you want this role?",
             "answer": "I enjoy platform engineering and automation.",
             "provenance": "grounded", "source_document_ids": ["doc-qa"]},
            {"question": "Tell me about a technical challenge you solved.",
             "answer": "I rebuilt a data pipeline from scratch.",
             "provenance": "grounded", "source_document_ids": ["doc-qa"]},
        ],
        "preferences": {
            "target_titles": ["Staff Platform Engineer"],
            "preferred_locations": ["Remote"],
            "remote_preference": "remote",
            "excluded_keywords": ["clearance"],
            "candidate_name": "Test Person",
            "work_authorization": "Authorized to work in the US",
            "minimum_compensation": "$180,000",
        },
    }


def _sample_lead() -> dict:
    return {
        "lead_id": "exampleco-staff-platform-abc123",
        "fingerprint": "abc123",
        "source": "greenhouse",
        "application_url": "https://example.com/jobs/123",
        "company": "ExampleCo",
        "title": "Staff Platform Engineer",
        "location": "Remote",
        "raw_description": "We need a staff platform engineer with Python, AWS, system design experience.",
        "normalized_requirements": {
            "required": ["python", "aws", "system design"],
            "preferred": ["kubernetes", "terraform"],
            "keywords": ["python", "aws", "platform", "system design", "kubernetes"],
        },
        "status": "discovered",
    }


class GenerationTokensTest(unittest.TestCase):
    def test_preserves_short_terms(self) -> None:
        result = generation_tokens("AI and ML are key, also Go and UI frameworks")
        self.assertIn("ai", result)
        self.assertIn("ml", result)
        self.assertIn("go", result)
        self.assertIn("ui", result)

    def test_standard_terms(self) -> None:
        result = generation_tokens("Python AWS Kubernetes")
        self.assertIn("python", result)
        self.assertIn("aws", result)
        self.assertIn("kubernetes", result)


class JaccardTest(unittest.TestCase):
    def test_empty_sets(self) -> None:
        self.assertEqual(_jaccard(set(), set()), 0.0)

    def test_identical_sets(self) -> None:
        self.assertEqual(_jaccard({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint_sets(self) -> None:
        self.assertEqual(_jaccard({"a"}, {"b"}), 0.0)

    def test_partial_overlap(self) -> None:
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        self.assertAlmostEqual(result, 2.0 / 4.0)


class AccomplishmentSelectionTest(unittest.TestCase):
    def test_variant_accomplishment_selection_differs(self) -> None:
        profile = _sample_profile()
        lead_keywords = {"python", "aws", "platform", "system", "design"}

        tech = select_accomplishments_for_variant(
            profile["experience_highlights"], lead_keywords, "technical_depth", limit=3)
        impact = select_accomplishments_for_variant(
            profile["experience_highlights"], lead_keywords, "impact_focused", limit=3)
        breadth = select_accomplishments_for_variant(
            profile["experience_highlights"], lead_keywords, "breadth", limit=3)

        # At least one variant should differ from the others.
        self.assertFalse(
            tech == impact == breadth,
            "All three variants selected identical accomplishments — no differentiation",
        )

    def test_variant_accomplishment_overlap_below_threshold(self) -> None:
        profile = _sample_profile()
        lead_keywords = {"python", "aws", "platform", "system", "design"}

        tech = set(select_accomplishments_for_variant(
            profile["experience_highlights"], lead_keywords, "technical_depth", limit=4))
        impact = set(select_accomplishments_for_variant(
            profile["experience_highlights"], lead_keywords, "impact_focused", limit=4))

        # Pairwise Jaccard < 0.8 (some overlap expected, but not identical).
        union = tech | impact
        if union:
            overlap = len(tech & impact) / len(union)
            self.assertLess(overlap, 0.8,
                            f"Overlap too high between tech_depth and impact_focused: {overlap:.2f}")

    def test_accomplishment_scoring_exact_beats_partial(self) -> None:
        highlights = [
            {"summary": "Built python aws platform system design", "source_document_ids": []},
            {"summary": "Wrote documentation and readme files", "source_document_ids": []},
        ]
        lead_keywords = {"python", "aws", "platform", "system", "design"}
        result = select_accomplishments_for_variant(highlights, lead_keywords, "technical_depth", limit=2)
        self.assertEqual(result[0], highlights[0]["summary"])

    def test_empty_highlights(self) -> None:
        result = select_accomplishments_for_variant([], {"python"}, "technical_depth")
        self.assertEqual(result, [])


class SkillSelectionTest(unittest.TestCase):
    def test_selects_relevant_skills(self) -> None:
        profile = _sample_profile()
        lead_keywords = {"python", "aws", "kubernetes"}
        result = select_skills_for_variant(profile["skills"], lead_keywords, "technical_depth", limit=3)
        self.assertEqual(len(result), 3)
        # Python and AWS should rank highest.
        skill_names_lower = [s.lower() for s in result]
        self.assertIn("python", skill_names_lower)


class ResumeGenerationTest(unittest.TestCase):
    def test_generate_resume_variants_produces_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            lead = _sample_lead()
            profile = _sample_profile()
            results = generate_resume_variants(
                lead, profile, ["technical_depth", "impact_focused", "breadth"], output_dir)

            self.assertEqual(len(results), 3)
            schema = json.loads(
                (ROOT / "schemas" / "generated-content.schema.json").read_text(encoding="utf-8"))
            for r in results:
                validate(r, schema)
                # Check files exist.
                self.assertTrue(Path(r["output_path"]).exists())
                json_path = Path(r["output_path"]).with_suffix(".json")
                self.assertTrue(json_path.exists())

    def test_each_variant_has_different_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            results = generate_resume_variants(
                _sample_lead(), _sample_profile(),
                ["technical_depth", "impact_focused"], Path(tmpdir))
            styles = {r["variant_style"] for r in results}
            self.assertEqual(styles, {"technical_depth", "impact_focused"})


class KnockoutMatchingTest(unittest.TestCase):
    def test_knockout_question_matched(self) -> None:
        prefs = {"work_authorization": "Authorized to work in the US"}
        result = match_question_to_knockout("Are you authorized to work in the US?", prefs)
        self.assertIsNotNone(result)
        self.assertEqual(result["category"], "work_authorization")
        self.assertTrue(result["matched"])

    def test_knockout_false_positive_resistance(self) -> None:
        prefs = {"work_authorization": "Yes"}
        result = match_question_to_knockout(
            "What is your experience with authorization systems?", prefs)
        # "authorization systems" should NOT match because the keyword is
        # "work authorization" / "authorized to work" — not bare "authorization".
        self.assertIsNone(result)

    def test_salary_knockout(self) -> None:
        prefs = {"minimum_compensation": "$180,000"}
        result = match_question_to_knockout("What is your expected salary?", prefs)
        self.assertIsNotNone(result)
        self.assertEqual(result["category"], "salary_expectations")

    def test_no_match_returns_none(self) -> None:
        result = match_question_to_knockout("What is your favorite color?", {})
        self.assertIsNone(result)


class BankMatchingTest(unittest.TestCase):
    def test_bank_matching_returns_best_match(self) -> None:
        bank = [
            {"question": "Why do you want this role?", "answer": "I enjoy it.",
             "provenance": "grounded", "source_document_ids": []},
            {"question": "Tell me about a time you failed.", "answer": "Once...",
             "provenance": "grounded", "source_document_ids": []},
        ]
        matches = match_question_to_bank("Why do you want this position?", bank)
        self.assertTrue(len(matches) > 0)
        self.assertEqual(matches[0][0]["question"], "Why do you want this role?")

    def test_bank_matching_adversarial(self) -> None:
        bank = _sample_profile()["question_bank"]
        matches = match_question_to_bank("Tell me about yourself", bank, threshold=0.3)
        # "Tell me about yourself" should have very low overlap with specific questions.
        for _, score in matches:
            self.assertLess(score, 0.5)


class AnswerSetGenerationTest(unittest.TestCase):
    def test_generate_answer_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lead = _sample_lead()
            profile = _sample_profile()
            questions = [
                "Are you authorized to work in the US?",
                "Why do you want this role?",
                "What is your favorite programming paradigm?",
            ]
            policy = {"allow_inferred_answers": True, "stop_if_required_fact_missing": True}
            result = generate_answer_set(lead, profile, questions, policy, Path(tmpdir))

            self.assertEqual(result["content_type"], "answer_set")
            self.assertEqual(len(result["answers"]), 3)
            # First answer should be grounded (knockout match).
            self.assertEqual(result["answers"][0]["provenance"], "grounded")
            self.assertFalse(result["blocked"])

    def test_missing_knockout_blocks_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lead = _sample_lead()
            profile = _sample_profile()
            # Remove work_authorization from preferences.
            profile["preferences"].pop("work_authorization", None)
            questions = ["Are you authorized to work in the US?"]
            policy = {"allow_inferred_answers": False, "stop_if_required_fact_missing": True}
            result = generate_answer_set(lead, profile, questions, policy, Path(tmpdir))
            self.assertTrue(result["blocked"])

    def test_no_match_uses_inference_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lead = _sample_lead()
            profile = _sample_profile()
            questions = ["What is your spirit animal?"]

            # With inference allowed.
            result = generate_answer_set(
                lead, profile, questions,
                {"allow_inferred_answers": True, "stop_if_required_fact_missing": False},
                Path(tmpdir) / "a")
            self.assertEqual(result["answers"][0]["provenance"], "weak_inference")

            # Without inference.
            result2 = generate_answer_set(
                lead, profile, questions,
                {"allow_inferred_answers": False, "stop_if_required_fact_missing": False},
                Path(tmpdir) / "b")
            self.assertTrue(result2["answers"][0].get("missing_fact", False))


class CoverLetterTest(unittest.TestCase):
    def test_generate_cover_letter_with_company_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lead = _sample_lead()
            profile = _sample_profile()
            company = {
                "company_id": "exampleco",
                "company_name": "ExampleCo",
                "industry": "SaaS",
                "tech_stack": ["Python", "AWS", "Kubernetes"],
            }
            result = generate_cover_letter(lead, profile, company, Path(tmpdir))
            self.assertEqual(result["content_type"], "cover_letter")
            self.assertTrue(Path(result["output_path"]).exists())
            md = Path(result["output_path"]).read_text(encoding="utf-8")
            self.assertIn("ExampleCo", md)

    def test_generate_cover_letter_without_company_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_cover_letter(_sample_lead(), _sample_profile(), None, Path(tmpdir))
            self.assertEqual(result["content_type"], "cover_letter")
            self.assertTrue(Path(result["output_path"]).exists())


if __name__ == "__main__":
    unittest.main()
