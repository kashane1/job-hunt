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

from job_hunt.pdf_export import (
    PDF_EXPORT_ERROR_CODES,
    PdfExportError,
    _render_inline,
    _safe_url_fetcher,
    markdown_to_html,
    resolve_content_record_path,
)


class MarkdownToHtmlTest(unittest.TestCase):
    def test_handles_h1_h2_h3_list_paragraph_bold(self) -> None:
        md = "# Title\n## Section\n### Subsection\n- Item one\n- Item two **bold**\n\nA paragraph."
        html = markdown_to_html(md)
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<h2>Section</h2>", html)
        self.assertIn("<h3>Subsection</h3>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li>Item one</li>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<p>A paragraph.</p>", html)
        self.assertIn("</ul>", html)

    def test_never_emits_script_tag(self) -> None:
        html = markdown_to_html("# <script>alert(1)</script>")
        self.assertNotIn("<script", html.lower())
        self.assertIn("&lt;script&gt;", html)

    def test_never_emits_style_tag_with_import(self) -> None:
        html = markdown_to_html("# <style>@import url(file:///etc/passwd)</style>")
        self.assertNotIn("<style", html.lower())
        self.assertIn("&lt;style&gt;", html)

    def test_never_emits_link_tag(self) -> None:
        html = markdown_to_html("# <link rel='stylesheet' href='http://evil/'>")
        self.assertNotIn("<link", html.lower())

    def test_never_emits_img_tag_from_markdown_image_syntax(self) -> None:
        # Our renderer ignores ![alt](src) — it must NOT emit <img>
        html = markdown_to_html("![evil](http://attacker/x.png)")
        self.assertNotIn("<img", html.lower())

    def test_bold_wraps_escaped_content(self) -> None:
        # Escape first, then bold substitution
        html = markdown_to_html("**<script>hi</script>**")
        self.assertIn("<strong>&lt;script&gt;hi&lt;/script&gt;</strong>", html)

    def test_render_inline_escapes_quotes_and_amp(self) -> None:
        out = _render_inline("He said \"hi\" & waved")
        self.assertIn("&quot;", out)
        self.assertIn("&amp;", out)

    def test_blank_line_closes_list(self) -> None:
        md = "- a\n- b\n\nparagraph"
        html = markdown_to_html(md)
        # <ul> should close before the <p>
        ul_close = html.index("</ul>")
        p_open = html.index("<p>")
        self.assertLess(ul_close, p_open)


class SafeUrlFetcherTest(unittest.TestCase):
    def test_rejects_file_scheme(self) -> None:
        with self.assertRaises(PdfExportError) as ctx:
            _safe_url_fetcher("file:///etc/passwd")
        self.assertEqual(ctx.exception.error_code, "pdf_fetch_blocked")

    def test_rejects_http_scheme(self) -> None:
        with self.assertRaises(PdfExportError) as ctx:
            _safe_url_fetcher("http://attacker/evil.css")
        self.assertEqual(ctx.exception.error_code, "pdf_fetch_blocked")

    def test_rejects_https_scheme(self) -> None:
        with self.assertRaises(PdfExportError) as ctx:
            _safe_url_fetcher("https://attacker/evil.css")
        self.assertEqual(ctx.exception.error_code, "pdf_fetch_blocked")


class PdfExportErrorTest(unittest.TestCase):
    def test_to_dict_contains_error_code(self) -> None:
        exc = PdfExportError("boom", error_code="render_failed", remediation="retry")
        self.assertEqual(exc.to_dict(), {
            "error_code": "render_failed",
            "message": "boom",
            "remediation": "retry",
            "url": "",
        })

    def test_error_codes_are_restricted(self) -> None:
        with self.assertRaises(AssertionError):
            PdfExportError("boom", error_code="made_up_code")

    def test_inherits_value_error(self) -> None:
        # Batch 1 convention: structured errors inherit ValueError (like ValidationError)
        exc = PdfExportError("boom", error_code="source_missing")
        self.assertIsInstance(exc, ValueError)

    def test_error_codes_frozenset(self) -> None:
        self.assertIn("weasyprint_missing", PDF_EXPORT_ERROR_CODES)
        self.assertIn("source_missing", PDF_EXPORT_ERROR_CODES)
        self.assertIn("render_failed", PDF_EXPORT_ERROR_CODES)
        self.assertIn("pdf_fetch_blocked", PDF_EXPORT_ERROR_CODES)


class ResolveContentRecordPathTest(unittest.TestCase):
    def test_mutually_exclusive_flags_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_content_record_path("x", "y", Path("/tmp"))

    def test_missing_record_raises_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PdfExportError) as ctx:
                resolve_content_record_path(
                    str(Path(tmp) / "nonexistent.json"), None, Path(tmp)
                )
            self.assertEqual(ctx.exception.error_code, "source_missing")

    def test_neither_flag_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                resolve_content_record_path(None, None, Path(tmp))

    def test_content_id_resolves_to_resumes_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            resumes = data_root / "generated" / "resumes"
            resumes.mkdir(parents=True)
            target = resumes / "abc123.json"
            target.write_text("{}")
            resolved = resolve_content_record_path(None, "abc123", data_root)
            self.assertEqual(resolved, target)

    def test_content_id_no_match_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PdfExportError) as ctx:
                resolve_content_record_path(None, "nonexistent-id", Path(tmp))
            self.assertEqual(ctx.exception.error_code, "source_missing")


class ExportPdfMissingWeasyprintTest(unittest.TestCase):
    """Test that graceful ImportError works — skipped when weasyprint IS installed."""

    def test_import_error_raises_structured_error(self) -> None:
        # If weasyprint is installed, simulate the import failure path
        import importlib
        import sys
        saved = sys.modules.pop("weasyprint", None)
        try:
            sys.modules["weasyprint"] = None  # type: ignore[assignment]
            # Need to reload pdf_export to get a fresh _weasyprint_or_raise call path
            from job_hunt.pdf_export import _weasyprint_or_raise
            with self.assertRaises(PdfExportError) as ctx:
                _weasyprint_or_raise()
            self.assertEqual(ctx.exception.error_code, "weasyprint_missing")
            self.assertIn("pip install", ctx.exception.remediation)
        finally:
            if saved is not None:
                sys.modules["weasyprint"] = saved
            else:
                sys.modules.pop("weasyprint", None)


if __name__ == "__main__":
    unittest.main()
