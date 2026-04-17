"""Batch 3 Phase 1 foundation tests — not covered elsewhere.

Covers: StructuredError uniformity, write_json concurrent writes, simple_yaml
list-of-mappings support, FetchResult shape, IPv4-mapped-IPv6 SSRF block.
"""

from __future__ import annotations

import ipaddress
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.ingestion import (
    FetchResult,
    IngestionError,
    _ip_is_disallowed,
    _validate_url_for_fetch,
)
from job_hunt.pdf_export import PdfExportError
from job_hunt.simple_yaml import (
    emit_watchlist_yaml,
    has_comments,
    loads as load_yaml,
)
from job_hunt.utils import StructuredError, write_json


class StructuredErrorUniformityTest(unittest.TestCase):
    def test_all_subclasses_share_base(self) -> None:
        ing = IngestionError("m", error_code="timeout", url="http://a", remediation="x")
        pdf = PdfExportError("m", error_code="render_failed", remediation="x")
        self.assertIsInstance(ing, StructuredError)
        self.assertIsInstance(pdf, StructuredError)

    def test_to_dict_shape_matches(self) -> None:
        ing = IngestionError("m", error_code="timeout", url="http://a", remediation="x")
        pdf = PdfExportError("m", error_code="render_failed", remediation="x")
        self.assertEqual(set(ing.to_dict()), {"error_code", "message", "url", "remediation"})
        self.assertEqual(set(pdf.to_dict()), {"error_code", "message", "url", "remediation"})

    def test_unknown_code_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            IngestionError("m", error_code="not_a_real_code")
        with self.assertRaises(AssertionError):
            PdfExportError("m", error_code="not_a_real_code")


class WriteJsonConcurrentTest(unittest.TestCase):
    def test_concurrent_writes_to_same_path_no_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shared.json"
            errors: list[Exception] = []

            def writer(value: int) -> None:
                try:
                    write_json(target, {"value": value, "filler": "x" * 256})
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            # File should exist and parse as valid JSON
            data = json.loads(target.read_text())
            self.assertIn("value", data)

    def test_no_tmp_stragglers_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp)
            target = dir_path / "foo.json"
            write_json(target, {"a": 1})
            tmps = list(dir_path.glob("*.tmp"))
            self.assertEqual(tmps, [], f"unexpected stragglers: {tmps}")


class SimpleYamlListOfMappingsTest(unittest.TestCase):
    def test_parses_list_of_mappings(self) -> None:
        text = """
companies:
  - name: "ExampleCo"
    greenhouse: "exampleco"
  - name: "Other"
    lever: "other"
"""
        out = load_yaml(text)
        self.assertEqual(len(out["companies"]), 2)
        self.assertEqual(out["companies"][0], {"name": "ExampleCo", "greenhouse": "exampleco"})
        self.assertEqual(out["companies"][1], {"name": "Other", "lever": "other"})

    def test_rejects_depth_3_nesting(self) -> None:
        text = """
companies:
  - name: "Co"
    nested:
      deeper: "nope"
"""
        with self.assertRaises(ValueError):
            load_yaml(text)

    def test_still_parses_scalar_list(self) -> None:
        text = """
tags:
  - "a"
  - "b"
"""
        self.assertEqual(load_yaml(text), {"tags": ["a", "b"]})

    def test_existing_configs_still_parse(self) -> None:
        text = """
skill_keywords:
  - python
  - ruby
answer_policy: "strict"
stop_if_confidence_below: 0.75
"""
        out = load_yaml(text)
        self.assertEqual(out["skill_keywords"], ["python", "ruby"])
        self.assertEqual(out["answer_policy"], "strict")
        self.assertEqual(out["stop_if_confidence_below"], 0.75)

    def test_has_comments_detection(self) -> None:
        self.assertTrue(has_comments("# hi\nkey: val\n"))
        self.assertTrue(has_comments("key: val\n# trailing\n"))
        self.assertFalse(has_comments("key: val\nother: x\n"))


class EmitWatchlistYamlTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        data = {
            "companies": [
                {"name": "ExampleCo", "greenhouse": "exampleco"},
                {"name": "Other", "lever": "other"},
            ],
            "filters": {
                "keywords_any": ["engineer", "dev"],
            },
        }
        text = emit_watchlist_yaml(data)
        parsed = load_yaml(text)
        self.assertEqual(parsed["companies"], data["companies"])
        self.assertEqual(parsed["filters"]["keywords_any"], ["engineer", "dev"])

    def test_escapes_control_chars(self) -> None:
        with self.assertRaises(ValueError):
            emit_watchlist_yaml({"companies": [{"name": "bad\x00"}]})

    def test_escapes_quotes_and_newlines(self) -> None:
        # Newline should be escaped to `\n`, not rendered literally.
        data = {"companies": [{"name": "X", "notes": 'line1\nline2"quote"'}]}
        text = emit_watchlist_yaml(data)
        self.assertIn("\\n", text)
        self.assertIn('\\"', text)


class FetchResultShapeTest(unittest.TestCase):
    def test_fetch_result_has_status_headers_body(self) -> None:
        r = FetchResult(status=200, headers={"content-type": "text/html"}, body="<p>ok</p>")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["content-type"], "text/html")
        self.assertEqual(r.body, "<p>ok</p>")


class Ipv4MappedIpv6Test(unittest.TestCase):
    def test_mapped_loopback_disallowed(self) -> None:
        mapped = ipaddress.ip_address("::ffff:127.0.0.1")
        self.assertTrue(_ip_is_disallowed(mapped))

    def test_mapped_private_disallowed(self) -> None:
        mapped = ipaddress.ip_address("::ffff:10.0.0.1")
        self.assertTrue(_ip_is_disallowed(mapped))

    def test_mapped_public_allowed(self) -> None:
        # 8.8.8.8 is a public Google DNS IP
        mapped = ipaddress.ip_address("::ffff:8.8.8.8")
        self.assertFalse(_ip_is_disallowed(mapped))


if __name__ == "__main__":
    unittest.main()
