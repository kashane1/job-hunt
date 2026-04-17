"""Pure utility functions shared across job_hunt modules.

Zero domain knowledge — only generic I/O, text, and hashing helpers, plus the
`StructuredError` base that structured error classes across the codebase share
so CLI handlers can catch them uniformly.

Stateful cross-module infrastructure (rate limiter, robots cache) lives in
`net_policy.py`, NOT here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Iterable

from .simple_yaml import loads as load_yaml


# =============================================================================
# StructuredError base — shared by IngestionError / PdfExportError / DiscoveryError
# =============================================================================

class StructuredError(ValueError):
    """Base for structured, agent-consumable errors at I/O/CLI boundaries.

    Every subclass defines `ALLOWED_ERROR_CODES` as a frozenset and carries
    `error_code`, `url`, `remediation`. CLI error handlers can catch this base
    class uniformly and emit `exc.to_dict()` as the error envelope.
    """

    ALLOWED_ERROR_CODES: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        message: str,
        error_code: str,
        url: str = "",
        remediation: str = "",
    ):
        super().__init__(message)
        assert error_code in self.ALLOWED_ERROR_CODES, f"unknown error_code: {error_code}"
        self.error_code = error_code
        self.url = url
        self.remediation = remediation

    def to_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "url": self.url,
            "remediation": self.remediation,
        }


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


def write_json(path: Path, payload) -> None:
    """Atomically write JSON using per-call unique tmp + parent-dir fsync.

    Safety invariants:
    - Per-call unique tmp via tempfile.mkstemp in the target directory — two
      concurrent writers to the same path cannot collide on a shared `.tmp`.
    - fsync the file AND (best-effort) the parent directory so os.replace is
      durable across crashes on Linux ext4. Parent-dir fsync is a no-op on
      platforms where os.open of a directory fails (e.g. Windows).
    - Cleans up the temp file on any exception via `except BaseException`.
    """
    ensure_dir(path.parent)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
    except BaseException:
        tmp_path.unlink(missing_ok=True)
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
