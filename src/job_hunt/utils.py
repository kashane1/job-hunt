"""Pure utility functions shared across job_hunt modules.

Zero domain knowledge — only generic I/O, text, and hashing helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from .simple_yaml import loads as load_yaml


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def display_path(path: Path) -> str:
    resolved = path.resolve()
    root = repo_root().resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically using write-to-temp-then-rename."""
    ensure_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = "\n---\n"
    end = text.find(marker, 4)
    if end == -1:
        return {}, text
    frontmatter = text[4:end]
    body = text[end + len(marker) :]
    return load_yaml(frontmatter), body


def load_yaml_file(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default or {}
    return load_yaml(path.read_text(encoding="utf-8"))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.-]{3,}", text.lower())


def unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def meaningful_lines(text: str, limit: int | None = None) -> list[str]:
    items: list[str] = []
    for raw_line in text.splitlines():
        stripped = " ".join(raw_line.strip().split())
        if not stripped:
            continue
        if stripped in {"___", ">>>", "________________", "_____________", "____________________"}:
            continue
        items.append(stripped)
        if limit is not None and len(items) >= limit:
            break
    return items
