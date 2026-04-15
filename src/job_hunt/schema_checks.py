"""Subset JSON-schema validation used for repository artifacts."""

from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    """Raised when data does not satisfy a schema."""


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def validate(data: Any, schema: dict, path: str = "$") -> None:
    # This intentionally validates only the schema features this repo uses.
    # Keeping it small avoids adding a third-party dependency for early v1.
    expected_type = schema.get("type")
    if expected_type and not _type_matches(expected_type, data):
        raise ValidationError(f"{path}: expected {expected_type}, got {type(data).__name__}")

    if "enum" in schema and data not in schema["enum"]:
        raise ValidationError(f"{path}: {data!r} not in enum {schema['enum']!r}")

    if expected_type == "object":
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                raise ValidationError(f"{path}: missing required key {key!r}")
        properties = schema.get("properties", {})
        for key, value in data.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")

    if expected_type == "array":
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(data):
                validate(item, item_schema, f"{path}[{index}]")
