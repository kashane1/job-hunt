from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.schema_checks import ValidationError, validate


class SchemaChecksTest(unittest.TestCase):
    def test_validate_accepts_matching_data(self) -> None:
        schema = {
            "type": "object",
            "required": ["name", "items"],
            "properties": {
                "name": {"type": "string"},
                "items": {"type": "array", "items": {"type": "integer"}},
            },
        }
        validate({"name": "ok", "items": [1, 2, 3]}, schema)

    def test_validate_rejects_missing_key(self) -> None:
        schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
        with self.assertRaises(ValidationError):
            validate({}, schema)


if __name__ == "__main__":
    unittest.main()
