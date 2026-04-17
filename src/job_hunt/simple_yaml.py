"""A tiny YAML subset parser for repo-local config/frontmatter.

Supported forms (intentionally narrow — avoid depending on a full YAML lib):

- Flat `key: value` pairs at top level.
- `key:` followed by indented `- scalar` items (top-level list of scalars).
- `key:` followed by indented `- name: value` items (list of mappings at depth 2).
  Each list item is an object delimited by the next `- ` at the same indent.
  Nested mappings inside a list-of-mappings are NOT supported — keeping the
  format capped at depth 2 prevents scope creep into full YAML semantics.

The `_emit_watchlist_yaml` writer emits a subset safe for config/watchlist.yaml:
double-quoted strings with quote/newline/control-char escaping, and list items
rendered as `- key: "value"` blocks. Comments from the source file are NOT
preserved (no comment node in the AST); callers must warn on comment loss.
"""

from __future__ import annotations

import re
from typing import Any


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


_KEY_RE = re.compile(r"^([A-Za-z0-9_./-]+):(?:\s*(.*))?$")


def _leading_spaces(raw_line: str) -> int:
    return len(raw_line) - len(raw_line.lstrip(" "))


def loads(text: str) -> dict:
    """Parse a narrow YAML subset.

    Scans lines once, branching on indent. Raises ValueError for any form
    beyond the documented subset so callers see failures early.
    """
    result: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if _leading_spaces(raw_line) != 0:
            raise ValueError(f"Unexpected indentation at top level: {raw_line!r}")
        match = _KEY_RE.match(stripped)
        if not match:
            raise ValueError(f"Unsupported YAML line: {raw_line!r}")
        key, value = match.groups()
        if value is not None and value != "":
            result[key] = _parse_scalar(value)
            i += 1
            continue

        # Collect child block starting at i + 1
        child_lines: list[str] = []
        j = i + 1
        while j < len(lines):
            child_raw = lines[j]
            child_stripped = child_raw.strip()
            if not child_stripped or child_stripped.startswith("#"):
                child_lines.append(child_raw)
                j += 1
                continue
            if _leading_spaces(child_raw) == 0:
                break
            child_lines.append(child_raw)
            j += 1

        result[key] = _parse_child_block(child_lines)
        i = j

    return result


def _parse_child_block(child_lines: list[str]):
    """Parse the indented block under a `key:` into a list OR a dict.

    Shape is auto-detected from the first non-blank child:
    - Starts with `- `  → a list (scalar items OR list-of-mappings, depth 2).
    - Otherwise         → a dict; each entry may have a value inline OR a nested
      list of scalars at depth 2.
    """
    first_indent: int | None = None
    first_is_dash = False
    for line in child_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            first_indent = _leading_spaces(line)
            first_is_dash = stripped.startswith("- ")
            break
    if first_indent is None:
        return []

    if first_is_dash:
        return _parse_list_block(child_lines, first_indent)
    return _parse_dict_block(child_lines, first_indent)


def _parse_list_block(child_lines: list[str], dash_indent: int) -> list:
    items: list = []
    i = 0
    while i < len(child_lines):
        raw = child_lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = _leading_spaces(raw)
        if indent != dash_indent:
            raise ValueError(f"Inconsistent list indentation at {raw!r}")
        if not stripped.startswith("- "):
            raise ValueError(f"Expected list item starting with '- ': {raw!r}")
        after_dash = stripped[2:]
        dash_key_match = _KEY_RE.match(after_dash)

        if dash_key_match:
            # `- key: value` or `- key:` — mapping item, possibly with continuation
            key = dash_key_match.group(1)
            raw_value = dash_key_match.group(2) or ""
            if raw_value == "":
                raise ValueError(
                    f"Nested mappings beyond depth 2 are not supported: {raw!r}"
                )
            mapping: dict[str, Any] = {key: _parse_scalar(raw_value)}
            i += 1
            continuation_indent = dash_indent + 2
            while i < len(child_lines):
                next_raw = child_lines[i]
                next_stripped = next_raw.strip()
                if not next_stripped or next_stripped.startswith("#"):
                    i += 1
                    continue
                next_indent = _leading_spaces(next_raw)
                if next_indent <= dash_indent:
                    break
                if next_indent != continuation_indent:
                    raise ValueError(
                        f"Nested mappings beyond depth 2 are not supported: {next_raw!r}"
                    )
                cont_match = _KEY_RE.match(next_stripped)
                if not cont_match:
                    raise ValueError(f"Unsupported continuation line: {next_raw!r}")
                cont_key, cont_value = cont_match.groups()
                if cont_value is None or cont_value == "":
                    raise ValueError(
                        f"Nested mappings beyond depth 2 are not supported: {next_raw!r}"
                    )
                mapping[cont_key] = _parse_scalar(cont_value)
                i += 1
            items.append(mapping)
            continue

        items.append(_parse_scalar(after_dash))
        i += 1
    return items


def _parse_dict_block(child_lines: list[str], key_indent: int) -> dict:
    result: dict = {}
    i = 0
    while i < len(child_lines):
        raw = child_lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = _leading_spaces(raw)
        if indent != key_indent:
            raise ValueError(f"Inconsistent dict indentation at {raw!r}")
        m = _KEY_RE.match(stripped)
        if not m:
            raise ValueError(f"Unsupported line in dict block: {raw!r}")
        key, value = m.groups()
        if value is not None and value != "":
            result[key] = _parse_scalar(value)
            i += 1
            continue

        # Nested block under this key — must be a list of scalars at depth 2
        i += 1
        nested: list[str] = []
        while i < len(child_lines):
            next_raw = child_lines[i]
            next_stripped = next_raw.strip()
            if not next_stripped or next_stripped.startswith("#"):
                nested.append(next_raw)
                i += 1
                continue
            next_indent = _leading_spaces(next_raw)
            if next_indent <= key_indent:
                break
            nested.append(next_raw)
            i += 1
        if not nested:
            result[key] = []
            continue
        nested_value = _parse_child_block(nested)
        if isinstance(nested_value, dict):
            raise ValueError(
                f"Nested mappings beyond depth 2 are not supported under {key!r}"
            )
        # Only scalar lists are allowed inside a dict-block value
        for item in nested_value:
            if isinstance(item, dict):
                raise ValueError(
                    f"Nested mappings beyond depth 2 are not supported under {key!r}"
                )
        result[key] = nested_value
    return result


# =============================================================================
# Safe writer — config/watchlist.yaml
# =============================================================================

_FORBIDDEN_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _yaml_quote_string(value: str) -> str:
    if _FORBIDDEN_CONTROL_CHARS_RE.search(value):
        raise ValueError(f"Control character in string: {value!r}")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_quote_string(str(value))


def emit_watchlist_yaml(data: dict) -> str:
    """Emit a narrow YAML dump suitable for config/watchlist.yaml round-trip.

    Layout:

        companies:
          - name: "ExampleCo"
            greenhouse: "exampleco"
          - name: "AnotherCorp"
            lever: "anothercorp"
        filters:
          keywords_any:
            - "engineer"
            - "developer"

    Every string goes through `_yaml_quote_string`. Does NOT preserve source
    comments (simple_yaml has no comment node); callers handle the loss.
    """
    lines: list[str] = []
    for key, val in data.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                if isinstance(item, dict):
                    subkeys = list(item.keys())
                    if not subkeys:
                        lines.append("  - {}")
                        continue
                    first_key = subkeys[0]
                    lines.append(f"  - {first_key}: {_render_value(item[first_key])}")
                    for sk in subkeys[1:]:
                        sub_val = item[sk]
                        lines.append(f"    {sk}: {_render_value(sub_val)}")
                else:
                    lines.append(f"  - {_render_value(item)}")
        elif isinstance(val, dict):
            lines.append(f"{key}:")
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, list):
                    lines.append(f"  {sub_key}:")
                    for item in sub_val:
                        lines.append(f"    - {_render_value(item)}")
                elif isinstance(sub_val, dict):
                    raise ValueError(
                        "emit_watchlist_yaml does not support dict-of-dict values"
                    )
                else:
                    lines.append(f"  {sub_key}: {_render_value(sub_val)}")
        else:
            lines.append(f"{key}: {_render_value(val)}")
    return "\n".join(lines) + "\n"


def has_comments(text: str) -> bool:
    """True iff any line of `text` is a YAML comment (starts with `#` after ws)."""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            return True
    return False
