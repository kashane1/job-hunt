"""On-demand markdown → PDF conversion for generated resumes and cover letters.

WeasyPrint is an optional dependency (install via `pip install 'job-hunt[pdf]'`).
The core pipeline works without it; only `export-pdf` requires it.

Security:
- All non-syntax text in markdown_to_html is HTML-escaped via html.escape() BEFORE
  any markup substitution. Prevents injection via fetched lead content.
- WeasyPrint is configured with a restricted url_fetcher that refuses file://,
  http://, https:// during rendering. Only inline data: URIs are permitted.
- base_url is explicitly None — no relative reference resolution.
"""

from __future__ import annotations

import html as html_module
import re
import types
from pathlib import Path
from typing import Final

from .utils import ensure_dir, now_iso, read_json, write_json

PDF_EXPORT_ERROR_CODES: Final = frozenset({
    "weasyprint_missing", "source_missing", "render_failed", "pdf_fetch_blocked",
})


class PdfExportError(ValueError):
    """Structured error with machine-readable error_code for agent consumption.
    Inherits ValueError per batch 1 convention (see ValidationError in schema_checks)."""

    def __init__(self, message: str, error_code: str, remediation: str = ""):
        super().__init__(message)
        assert error_code in PDF_EXPORT_ERROR_CODES, f"unknown error_code: {error_code}"
        self.error_code = error_code
        self.remediation = remediation

    def to_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "remediation": self.remediation,
        }


RESUME_CSS = """
@page { size: Letter; margin: 0.6in 0.7in; }
body { font-family: "DejaVu Sans", "Helvetica Neue", Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.35; color: #111; }
h1 { font-size: 18pt; margin: 0 0 0.1in 0; border-bottom: 1px solid #444; }
h2 { font-size: 12pt; margin: 0.15in 0 0.05in 0; text-transform: uppercase;
     letter-spacing: 0.04em; border-bottom: 0.5px solid #999; }
h3 { font-size: 11pt; margin: 0.1in 0 0.02in 0; }
ul { margin: 0.02in 0 0.08in 0.2in; padding: 0; }
li { margin: 0.02in 0; }
p { margin: 0.05in 0; }
strong { font-weight: 600; }
"""

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _weasyprint_or_raise() -> types.ModuleType:
    try:
        import weasyprint
        return weasyprint
    except ImportError as exc:
        raise PdfExportError(
            "weasyprint is not installed",
            error_code="weasyprint_missing",
            remediation="pip install 'job-hunt[pdf]'  (or: pip install weasyprint)",
        ) from exc


def _safe_url_fetcher(url: str) -> dict:
    """Refuses file://, http://, https:// and any non-data URL during WeasyPrint
    rendering. Prevents SSRF via @import / url() / @font-face in user-influenced
    content. Only data: URIs (inline images) pass through to the default fetcher."""
    if not url.startswith("data:"):
        raise PdfExportError(
            f"WeasyPrint tried to fetch {url!r} during rendering; blocked for safety",
            error_code="pdf_fetch_blocked",
        )
    from weasyprint.urls import default_url_fetcher
    return default_url_fetcher(url)


def _render_inline(text: str) -> str:
    """Escape first, then apply bold markup. Ordering ensures HTML special chars
    in input (e.g. <script>) become &lt;script&gt; BEFORE any markup is added."""
    escaped = html_module.escape(text, quote=True)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def markdown_to_html(md_text: str) -> str:
    """Minimal markdown → HTML renderer.

    Handles the bounded set of markdown that generation.py emits:
    - #, ##, ### headings
    - `- ` unordered lists
    - **bold**
    - paragraphs
    - blank-line separation

    Security invariants (enforced by tests):
    - <script>, <style>, <link>, <img> NEVER appear in output regardless of input
    - Every non-syntax text goes through html.escape(text, quote=True)
    - Adding a new markdown construct requires extending this renderer AND the
      negative-invariant tests.
    """
    lines_out: list[str] = []
    in_list = False
    for line in md_text.splitlines():
        stripped = line.rstrip()
        if stripped.startswith("### "):
            if in_list:
                lines_out.append("</ul>")
                in_list = False
            lines_out.append(f"<h3>{_render_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                lines_out.append("</ul>")
                in_list = False
            lines_out.append(f"<h2>{_render_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            if in_list:
                lines_out.append("</ul>")
                in_list = False
            lines_out.append(f"<h1>{_render_inline(stripped[2:])}</h1>")
        elif stripped.startswith("- "):
            if not in_list:
                lines_out.append("<ul>")
                in_list = True
            lines_out.append(f"<li>{_render_inline(stripped[2:])}</li>")
        elif not stripped:
            if in_list:
                lines_out.append("</ul>")
                in_list = False
        else:
            if in_list:
                lines_out.append("</ul>")
                in_list = False
            lines_out.append(f"<p>{_render_inline(stripped)}</p>")
    if in_list:
        lines_out.append("</ul>")
    return "\n".join(lines_out)


def export_pdf(content_record_path: Path) -> dict:
    """Read the generated-content record, convert its .md to .pdf, update the
    record's pdf_path field atomically, return the updated record.

    Raises PdfExportError with structured error_code on any failure.
    """
    weasyprint = _weasyprint_or_raise()
    record = read_json(content_record_path)
    md_path_str = record.get("output_path", "")
    if not md_path_str:
        raise PdfExportError(
            "Content record has no output_path",
            error_code="source_missing",
            remediation="Regenerate the content via generate-resume or generate-cover-letter.",
        )
    md_path = Path(md_path_str)
    if not md_path.exists():
        raise PdfExportError(
            f"Markdown source not found: {md_path}",
            error_code="source_missing",
            remediation="Re-run generate-resume or generate-cover-letter for this lead.",
        )
    pdf_path = md_path.with_suffix(".pdf")
    body_html = markdown_to_html(md_path.read_text(encoding="utf-8"))
    full_html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{RESUME_CSS}</style></head><body>{body_html}</body></html>"
    )
    try:
        weasyprint.HTML(
            string=full_html,
            url_fetcher=_safe_url_fetcher,
            base_url=None,
        ).write_pdf(str(pdf_path))
    except PdfExportError:
        raise
    except Exception as exc:
        raise PdfExportError(
            f"WeasyPrint failed to render {md_path}: {exc}",
            error_code="render_failed",
        ) from exc
    ensure_dir(pdf_path.parent)
    record["pdf_path"] = str(pdf_path)
    record["pdf_generated_at"] = now_iso()
    write_json(content_record_path, record)
    return record


def resolve_content_record_path(
    content_record: str | None,
    content_id: str | None,
    data_root: Path,
) -> Path:
    """Resolve either --content-record PATH or --content-id ID to a concrete
    content record file. Per pattern review: --content-record is the primary
    flag (matches batch 1 convention); --content-id is a convenience alternative
    that searches data/generated/{resumes,cover-letters,answers}/ for a match."""
    if content_record and content_id:
        raise ValueError("Cannot specify both --content-record and --content-id")
    if content_record:
        path = Path(content_record)
        if not path.exists():
            raise PdfExportError(
                f"Content record not found: {path}",
                error_code="source_missing",
            )
        return path
    if not content_id:
        raise ValueError("Must specify either --content-record PATH or --content-id ID")
    # Search common generated directories for a matching content_id
    for subdir in ("resumes", "cover-letters", "answers"):
        candidate = data_root / "generated" / subdir / f"{content_id}.json"
        if candidate.exists():
            return candidate
    raise PdfExportError(
        f"No content record found for content_id {content_id!r} in data/generated/",
        error_code="source_missing",
        remediation="Use --content-record PATH to point at the exact record file.",
    )
