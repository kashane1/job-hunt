"""A tiny YAML subset parser for repo-local config/frontmatter."""

from __future__ import annotations

import re


def _parse_scalar(raw: str):
    value = raw.strip()
    if value == "":
        return ""
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def loads(text: str) -> dict:
    # We only need a narrow YAML subset for local frontmatter and config:
    # flat key/value pairs plus top-level lists.
    result: dict = {}
    current_list_key: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"List item without key: {raw_line!r}")
            result.setdefault(current_list_key, [])
            result[current_list_key].append(_parse_scalar(stripped[2:]))
            continue

        match = re.match(r"^([A-Za-z0-9_./-]+):(?:\s*(.*))?$", stripped)
        if not match:
            raise ValueError(f"Unsupported YAML line: {raw_line!r}")

        key, value = match.groups()
        if value is None or value == "":
            result[key] = []
            current_list_key = key
        else:
            result[key] = _parse_scalar(value)
            current_list_key = None

    return result
