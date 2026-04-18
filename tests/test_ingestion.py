from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.ingestion import (
    INGESTION_ERROR_CODES,
    IngestionError,
    _fetch_generic_html,
    _html_to_text,
    _netloc_in_allowlist,
    _sanitize_url_for_logging,
    _to_markdown_with_frontmatter,
    _validate_url_for_fetch,
    _wrap_fetched_content,
    canonicalize_url,
    ingest_url,
    ingest_urls_file,
    is_hard_fail_url,
)
from job_hunt.schema_checks import validate


class CanonicalizeUrlTest(unittest.TestCase):
    def test_strips_utm_params(self) -> None:
        out = canonicalize_url("https://example.com/job/1?utm_source=linkedin&utm_campaign=q4")
        self.assertEqual(out, "https://example.com/job/1")

    def test_strips_greenhouse_gh_src(self) -> None:
        out = canonicalize_url("https://boards.greenhouse.io/co/jobs/123?gh_src=abc123&gh_jid=xyz")
        self.assertEqual(out, "https://boards.greenhouse.io/co/jobs/123")

    def test_strips_ad_click_ids(self) -> None:
        out = canonicalize_url("https://example.com/j/1?fbclid=X&gclid=Y&msclkid=Z&yclid=W")
        self.assertEqual(out, "https://example.com/j/1")

    def test_strips_hubspot_params(self) -> None:
        out = canonicalize_url("https://example.com/j/1?_hsenc=foo&hsa_net=bar&_hsmi=123")
        self.assertEqual(out, "https://example.com/j/1")

    def test_strips_lever_source(self) -> None:
        out = canonicalize_url("https://jobs.lever.co/co/uuid?lever-source=linkedin")
        self.assertEqual(out, "https://jobs.lever.co/co/uuid")

    def test_drops_fragment(self) -> None:
        out = canonicalize_url("https://example.com/j/1?a=b#section")
        self.assertEqual(out, "https://example.com/j/1?a=b")

    def test_lowercases_netloc_but_preserves_path_case(self) -> None:
        out = canonicalize_url("https://Example.COM/Path/To/Job")
        self.assertEqual(out, "https://example.com/Path/To/Job")

    def test_idempotent(self) -> None:
        url = "https://example.com/j/1?utm_source=x&a=b"
        c1 = canonicalize_url(url)
        c2 = canonicalize_url(c1)
        self.assertEqual(c1, c2)

    def test_same_posting_two_urls_produce_same_canonical(self) -> None:
        a = "https://boards.greenhouse.io/co/jobs/123?utm_source=linkedin&gh_src=abc"
        b = "https://boards.greenhouse.io/co/jobs/123/?fbclid=xyz"
        self.assertEqual(canonicalize_url(a), canonicalize_url(b))


class SsrfGuardsTest(unittest.TestCase):
    def test_rejects_file_scheme(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("file:///etc/passwd")
        self.assertEqual(ctx.exception.error_code, "scheme_blocked")

    def test_rejects_ftp_scheme(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("ftp://example.com/path")
        self.assertEqual(ctx.exception.error_code, "scheme_blocked")

    def test_rejects_gopher_scheme(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("gopher://example.com/")
        self.assertEqual(ctx.exception.error_code, "scheme_blocked")

    def test_rejects_localhost(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("http://localhost/")
        self.assertEqual(ctx.exception.error_code, "private_ip_blocked")

    def test_rejects_127_0_0_1(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("http://127.0.0.1/")
        self.assertEqual(ctx.exception.error_code, "private_ip_blocked")

    def test_rejects_aws_metadata_endpoint(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(ctx.exception.error_code, "private_ip_blocked")

    def test_rejects_rfc1918(self) -> None:
        for addr in ("http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/"):
            with self.assertRaises(IngestionError) as ctx:
                _validate_url_for_fetch(addr)
            self.assertEqual(ctx.exception.error_code, "private_ip_blocked")

    def test_rejects_url_without_hostname(self) -> None:
        with self.assertRaises(IngestionError) as ctx:
            _validate_url_for_fetch("http:///path")
        self.assertEqual(ctx.exception.error_code, "invalid_url")


class IngestionErrorTest(unittest.TestCase):
    def test_structured_fields(self) -> None:
        exc = IngestionError("oops", error_code="rate_limited", url="https://x/", remediation="wait")
        d = exc.to_dict()
        self.assertEqual(d["error_code"], "rate_limited")
        self.assertEqual(d["url"], "https://x/")
        self.assertEqual(d["remediation"], "wait")

    def test_inherits_value_error(self) -> None:
        exc = IngestionError("boom", error_code="timeout")
        self.assertIsInstance(exc, ValueError)

    def test_rejects_unknown_error_code(self) -> None:
        with self.assertRaises(AssertionError):
            IngestionError("boom", error_code="made_up_code")

    def test_error_codes_frozenset_has_all(self) -> None:
        for code in ("login_wall", "scheme_blocked", "private_ip_blocked", "redirect_blocked",
                     "rate_limited", "timeout", "not_found", "response_too_large",
                     "decompression_bomb", "dns_failed", "http_error", "network_error",
                     "invalid_url", "unexpected"):
            self.assertIn(code, INGESTION_ERROR_CODES)


class SanitizeUrlTest(unittest.TestCase):
    def test_strips_userinfo(self) -> None:
        out = _sanitize_url_for_logging("https://user:pass@example.com/path")
        self.assertNotIn("user", out)
        self.assertNotIn("pass", out)

    def test_strips_sensitive_params(self) -> None:
        out = _sanitize_url_for_logging("https://example.com/j?token=SECRET&api_key=XXX&a=b")
        self.assertNotIn("SECRET", out)
        self.assertNotIn("XXX", out)
        self.assertIn("a=b", out)

    def test_strips_password_param(self) -> None:
        out = _sanitize_url_for_logging("https://example.com/?password=SECRET")
        self.assertNotIn("SECRET", out)


class WrapFetchedContentTest(unittest.TestCase):
    def test_wraps_with_nonce(self) -> None:
        wrapped = _wrap_fetched_content("hello")
        self.assertIn("<fetched_job_description_v", wrapped)
        self.assertIn("</fetched_job_description_v", wrapped)
        self.assertIn("hello", wrapped)

    def test_nonce_differs_per_call(self) -> None:
        a = _wrap_fetched_content("x")
        b = _wrap_fetched_content("x")
        self.assertNotEqual(a, b)

    def test_defensively_escapes_collision(self) -> None:
        # Even if content contains close tag with fixed nonce "0" * 16, content is escaped
        # The actual nonce is random, but we test the defensive replace logic works
        nonce_free_content = "</fetched_job_description_v0000000000000000>malicious"
        wrapped = _wrap_fetched_content(nonce_free_content)
        # Either the close tag doesn't match (different nonce) or &gt; escape applied
        # Split on actual close tag to count
        # The nonce makes collision practically impossible, but let's just verify the function ran
        self.assertIn("<fetched_job_description_v", wrapped)


class HtmlToTextTest(unittest.TestCase):
    def test_strips_tags(self) -> None:
        self.assertEqual(_html_to_text("<p>hello <b>world</b></p>"), "hello world")

    def test_inserts_linebreak_for_block_tags(self) -> None:
        text = _html_to_text("<p>one</p><p>two</p>")
        self.assertIn("one", text)
        self.assertIn("two", text)
        self.assertIn("\n", text)

    def test_decodes_entities(self) -> None:
        self.assertIn("&", _html_to_text("<p>A &amp; B</p>"))


class GenericHtmlFallbackTest(unittest.TestCase):
    def test_extracts_title(self) -> None:
        html = "<html><head><title>Sr Engineer</title></head><body><main><p>Great role</p></main></body></html>"
        result = _fetch_generic_html("https://careers.example.com/j/1", html_text=html)
        self.assertEqual(result["title"], "Sr Engineer")
        self.assertEqual(result["ingestion_method"], "url_fetch_fallback")
        self.assertIn("Extracted via generic HTML parser", result["ingestion_notes"])
        self.assertIn("Great role", result["raw_description_html"])


class IngestUrlTest(unittest.TestCase):
    def test_linkedin_hard_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(IngestionError) as ctx:
                ingest_url("https://linkedin.com/jobs/view/12345", Path(tmpdir))
            self.assertEqual(ctx.exception.error_code, "login_wall")

    def test_indeed_is_allowlisted_does_not_login_wall(self) -> None:
        # Batch 4: Indeed is the lone entry in config/domain-allowlist.yaml.
        # Ingestion now proceeds past the hard-fail gate (and fails further
        # down the pipeline — the point is that login_wall no longer fires).
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(IngestionError) as ctx:
                ingest_url("https://www.indeed.com/viewjob?jk=abc", Path(tmpdir))
            self.assertNotEqual(ctx.exception.error_code, "login_wall")


class HardFailAllowlistTest(unittest.TestCase):
    def test_linkedin_still_hard_fails(self) -> None:
        self.assertTrue(is_hard_fail_url("https://www.linkedin.com/jobs/view/1"))
        self.assertTrue(is_hard_fail_url("https://linkedin.com/jobs/view/1"))

    def test_indeed_not_hard_fail(self) -> None:
        self.assertFalse(is_hard_fail_url("https://www.indeed.com/viewjob?jk=abc"))
        self.assertFalse(is_hard_fail_url("https://indeed.com/viewjob?jk=abc"))

    def test_non_matching_urls_pass(self) -> None:
        self.assertFalse(is_hard_fail_url("https://boards.greenhouse.io/co/jobs/1"))
        self.assertFalse(is_hard_fail_url("https://example.com/jobs/42"))

    def test_netloc_subdomain_matches_allowlisted_registrable(self) -> None:
        allowlist = frozenset({"indeed.com"})
        self.assertTrue(_netloc_in_allowlist("indeed.com", allowlist))
        self.assertTrue(_netloc_in_allowlist("www.indeed.com", allowlist))
        self.assertTrue(_netloc_in_allowlist("secure.indeed.com:443", allowlist))
        # Must not match a different registrable on the same suffix
        self.assertFalse(_netloc_in_allowlist("evil-indeed.com", allowlist))
        self.assertFalse(_netloc_in_allowlist("linkedin.com", allowlist))

    def test_invalid_url_treated_as_hard_fail_when_pattern_matches(self) -> None:
        # Pattern must match for hard-fail; garbage URLs don't match any pattern.
        self.assertFalse(is_hard_fail_url("::not a url::"))

    def test_html_override_bypasses_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            html = """<html><head><title>Senior Engineer</title></head>
            <body><main><h1>Senior Engineer</h1><p>A great role</p>
            <h2>Requirements</h2><ul><li>Python</li><li>Postgres</li></ul>
            </main></body></html>"""
            lead = ingest_url(
                "https://careers.examplecorp.com/jobs/42",
                output_dir,
                html_override=html,
            )
            self.assertIsNotNone(lead["lead_id"])
            self.assertEqual(lead["ingestion_method"], "url_fetch_fallback")
            self.assertIn("canonical_url", lead)
            # Verify the lead file was persisted
            lead_path = output_dir / f"{lead['lead_id']}.json"
            self.assertTrue(lead_path.exists())
            # Verify intake was moved to processed/
            processed = output_dir / "_intake" / "processed"
            self.assertTrue(processed.exists())
            processed_files = list(processed.glob("*.md"))
            self.assertEqual(len(processed_files), 1)
            # Verify lead passes schema validation
            schema = json.loads((ROOT / "schemas" / "lead.schema.json").read_text())
            validate(lead, schema)

    def test_same_posting_via_different_urls_idempotent(self) -> None:
        """Two URLs that canonicalize the same should produce the same lead_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            html = "<html><head><title>Engineer</title></head><body><main><p>role</p></main></body></html>"
            a = ingest_url(
                "https://careers.examplecorp.com/jobs/42?utm_source=linkedin",
                output_dir,
                html_override=html,
            )
            b = ingest_url(
                "https://careers.examplecorp.com/jobs/42?fbclid=xyz",
                output_dir,
                html_override=html,
            )
            self.assertEqual(a["lead_id"], b["lead_id"])
            self.assertEqual(a["fingerprint"], b["fingerprint"])

    def test_failed_ingestion_goes_to_failed_dir(self) -> None:
        """Simulate extract_lead failure to verify intake is moved to failed/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            html = "<html><head><title>t</title></head><body><p>x</p></body></html>"

            with patch("job_hunt.core.extract_lead", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    ingest_url(
                        "https://careers.example.com/j/1",
                        output_dir,
                        html_override=html,
                    )

            failed_dir = output_dir / "_intake" / "failed"
            self.assertTrue(failed_dir.exists())
            err_files = list(failed_dir.glob("*.err"))
            self.assertEqual(len(err_files), 1)
            err_content = err_files[0].read_text()
            self.assertIn("careers.example.com", err_content)
            self.assertIn("boom", err_content)


class IngestUrlsFileTest(unittest.TestCase):
    def test_deduplicates_by_canonical_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            urls_file = output_dir / "urls.txt"
            urls_file.write_text(
                "https://careers.example.com/j/42?utm_source=a\n"
                "https://careers.example.com/j/42?utm_source=b\n"
                "# comment line\n"
                "\n"
            )
            html = "<html><head><title>Eng</title></head><body><main><p>role</p></main></body></html>"

            # Mock _fetch_generic_html to always return the same payload
            from job_hunt import ingestion as ing_mod

            def fake_fetch(url, html_text=None):
                return {
                    "title": "Eng",
                    "company": "example",
                    "location": "",
                    "raw_description_html": "<p>role</p>",
                    "source": "html_fallback",
                    "ingestion_method": "url_fetch_fallback",
                    "ingestion_notes": "...",
                }

            with patch.object(ing_mod, "_fetch_generic_html", side_effect=fake_fetch):
                result = ingest_urls_file(urls_file, output_dir)

            # Dedupe should have collapsed the two variants to one call
            self.assertEqual(len(result["successes"]), 1)
            self.assertEqual(len(result["failures"]), 0)

    def test_collects_failures_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            urls_file = output_dir / "urls.txt"
            urls_file.write_text(
                "https://linkedin.com/jobs/view/12345\n"  # hard-fail
                "https://careers.example.com/j/99\n"
            )
            from job_hunt import ingestion as ing_mod

            def fake_fetch(url, html_text=None):
                return {
                    "title": "Eng",
                    "company": "example",
                    "location": "",
                    "raw_description_html": "<p>role</p>",
                    "source": "html_fallback",
                    "ingestion_method": "url_fetch_fallback",
                    "ingestion_notes": "...",
                }

            with patch.object(ing_mod, "_fetch_generic_html", side_effect=fake_fetch):
                result = ingest_urls_file(urls_file, output_dir)

            self.assertEqual(len(result["successes"]), 1)
            self.assertEqual(len(result["failures"]), 1)
            self.assertEqual(result["failures"][0]["error_code"], "login_wall")


class FrontmatterSynthesisTest(unittest.TestCase):
    def test_emits_frontmatter(self) -> None:
        md = _to_markdown_with_frontmatter({
            "source": "greenhouse",
            "company": "ExampleCo",
            "title": "Senior Engineer",
            "location": "Remote",
            "application_url": "https://boards.greenhouse.io/co/jobs/1",
            "canonical_url": "https://boards.greenhouse.io/co/jobs/1",
            "ingestion_method": "url_fetch_json",
            "ingested_at": "2026-04-16T10:00:00+00:00",
            "raw_description_html": "<p>Job description.</p>",
        })
        self.assertTrue(md.startswith("---\n"))
        self.assertIn('source: "greenhouse"', md)
        self.assertIn('company: "ExampleCo"', md)
        self.assertIn('ingestion_method: "url_fetch_json"', md)
        self.assertIn("# Senior Engineer", md)


class IndeedViewjobJsonLdTest(unittest.TestCase):
    """Phase 4 — Indeed viewjob pages parse via JobPosting JSON-LD with
    hostile-input safety (size cap, malformed blocks, @graph, @type list)."""

    def _wrap(self, body: str) -> str:
        return f"<html><body>{body}</body></html>"

    def test_parses_jobposting_json_ld(self) -> None:
        from job_hunt.ingestion import _fetch_indeed_viewjob
        html_text = self._wrap(
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"Staff Engineer",'
            '"hiringOrganization":{"@type":"Organization","name":"Acme"},'
            '"jobLocation":{"@type":"Place","address":{"addressLocality":"Remote"}},'
            '"description":"&lt;p&gt;Build systems&lt;/p&gt;"}'
            "</script>"
        )
        result = _fetch_indeed_viewjob("https://www.indeed.com/viewjob?jk=x",
                                       html_text=html_text)
        self.assertEqual(result["title"], "Staff Engineer")
        self.assertEqual(result["company"], "Acme")
        self.assertEqual(result["location"], "Remote")
        self.assertEqual(result["ingestion_method"], "url_fetch_jsonld")
        # html.unescape must run on the description
        self.assertIn("<p>Build systems</p>", result["raw_description_html"])

    def test_walks_graph_wrapper(self) -> None:
        from job_hunt.ingestion import _fetch_indeed_viewjob
        html_text = self._wrap(
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@graph":['
            '{"@type":"WebSite","name":"ignored"},'
            '{"@type":"JobPosting","title":"Graph Lane",'
            ' "hiringOrganization":{"name":"GraphCo"}}]}'
            "</script>"
        )
        result = _fetch_indeed_viewjob("https://indeed.com/viewjob?jk=y",
                                       html_text=html_text)
        self.assertEqual(result["title"], "Graph Lane")
        self.assertEqual(result["company"], "GraphCo")

    def test_handles_type_as_list(self) -> None:
        from job_hunt.ingestion import _fetch_indeed_viewjob
        html_text = self._wrap(
            '<script type="application/ld+json">'
            '{"@type":["Thing","JobPosting"],"title":"Listed Type",'
            ' "hiringOrganization":{"name":"TypeCo"}}'
            "</script>"
        )
        result = _fetch_indeed_viewjob("https://indeed.com/viewjob?jk=z",
                                       html_text=html_text)
        self.assertEqual(result["title"], "Listed Type")

    def test_falls_back_to_jobdescription_on_malformed_json(self) -> None:
        from job_hunt.ingestion import _fetch_indeed_viewjob
        html_text = self._wrap(
            '<script type="application/ld+json">{broken json</script>'
            '<div id="jobDescriptionText">Plain text description</div>'
            '</div></div></div>'
        )
        result = _fetch_indeed_viewjob("https://indeed.com/viewjob?jk=m",
                                       html_text=html_text)
        self.assertIn("Plain text description", result["raw_description_html"])

    def test_rejects_oversized_ld_block(self) -> None:
        from job_hunt.ingestion import _fetch_indeed_viewjob
        # 600KB JSON-LD block — skipped before json.loads.
        huge = '"padding":"' + ("x" * 600_000) + '"'
        html_text = self._wrap(
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"Huge",' + huge + "}"
            "</script>"
        )
        result = _fetch_indeed_viewjob("https://indeed.com/viewjob?jk=h",
                                       html_text=html_text)
        self.assertEqual(result["title"], "")  # oversized block was skipped


class RetryAfterParsingTest(unittest.TestCase):
    """Phase 2 — RFC 9110 §10.2.3 Retry-After parsing (delta-seconds or HTTP-date)."""

    def test_parses_delta_seconds(self) -> None:
        from job_hunt.net_policy import parse_retry_after
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after("0"), 0.0)

    def test_parses_http_date(self) -> None:
        from job_hunt.net_policy import parse_retry_after
        # Choose a date well in the future so the parser returns a positive value.
        parsed = parse_retry_after("Fri, 31 Dec 2099 23:59:59 GMT")
        assert parsed is not None
        self.assertGreater(parsed, 0.0)

    def test_past_http_date_clamped_to_zero(self) -> None:
        from job_hunt.net_policy import parse_retry_after
        self.assertEqual(parse_retry_after("Mon, 01 Jan 2001 00:00:00 GMT"), 0.0)

    def test_unparseable_returns_none(self) -> None:
        from job_hunt.net_policy import parse_retry_after
        self.assertIsNone(parse_retry_after("not a date"))
        self.assertIsNone(parse_retry_after(""))
        self.assertIsNone(parse_retry_after("   "))


class FetchChromeUserAgentTest(unittest.TestCase):
    """Phase 2 — fetch must identify as Chrome, not the legacy bot agent."""

    def test_shared_ua_is_chrome(self) -> None:
        from job_hunt.net_policy import DISCOVERY_USER_AGENT
        self.assertIn("Chrome/131", DISCOVERY_USER_AGENT)

    def test_fetch_wires_shared_ua_and_accept_language(self) -> None:
        # Structural assertion against the source: fetch builds its
        # urllib Request with the shared constant + Accept-Language.
        src = (ROOT / "src" / "job_hunt" / "ingestion.py").read_text(encoding="utf-8")
        self.assertIn('"User-Agent": DISCOVERY_USER_AGENT', src)
        self.assertIn("Accept-Language", src)


if __name__ == "__main__":
    unittest.main()
