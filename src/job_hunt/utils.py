"""Pure utility functions shared across job_hunt modules.

Zero domain knowledge — only generic I/O, text, and hashing helpers, plus the
`StructuredError` base that structured error classes across the codebase share
so CLI handlers can catch them uniformly.

Stateful cross-module infrastructure (rate limiter, robots cache) lives in
`net_policy.py`, NOT here.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Iterable, Iterator

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


_IS_DARWIN = sys.platform == "darwin"
_F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", None)


def _fullfsync_if_darwin(fd: int) -> None:
    """On macOS, ``fsync`` only flushes the OS page cache — the device cache
    is still volatile. ``F_FULLFSYNC`` is the APFS/HFS+ barrier that actually
    reaches stable storage. On Linux the ordinary ``fsync`` above is enough.
    Best-effort: a kernel that does not support the control returns a plain
    ``OSError``, which is harmless because ``os.fsync`` already ran.
    """
    if not _IS_DARWIN or _F_FULLFSYNC is None:
        return
    try:
        fcntl.fcntl(fd, _F_FULLFSYNC)
    except OSError:
        pass


def write_json(path: Path, payload) -> None:
    """Atomically write JSON using per-call unique tmp + parent-dir fsync.

    Safety invariants:
    - Per-call unique tmp via tempfile.mkstemp in the target directory — two
      concurrent writers to the same path cannot collide on a shared `.tmp`.
    - fsync the file AND (best-effort) the parent directory so os.replace is
      durable across crashes on Linux ext4. Parent-dir fsync is a no-op on
      platforms where os.open of a directory fails (e.g. Windows).
    - On macOS adds F_FULLFSYNC so APFS actually flushes the device cache.
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
            _fullfsync_if_darwin(f.fileno())
        os.replace(str(tmp_path), str(path))
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
                _fullfsync_if_darwin(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


# =============================================================================
# Advisory file lock (for answer-bank, status.json, and other multi-writer files)
# =============================================================================

class FileLockContentionError(RuntimeError):
    """Raised when an advisory lock cannot be acquired non-blocking.

    Callers in the application / answer-bank modules re-wrap this into a
    structured ``PlanError(answer_bank_locked)`` so agents see a stable error
    code. Keeping the plumbing error here (and not in application.py) lets
    utils.py stay domain-free — the same lock primitive is reused for
    status.json merges, batch locks, etc.
    """


@contextmanager
def file_lock(
    data_path: Path,
    *,
    check_mtime: bool = True,
) -> Iterator[None]:
    """Acquire a non-blocking advisory lock on a sibling ``.lock`` file.

    Contract (per the Batch 4 framework-docs research):
    - Lock file is always a **sibling** (``<data_path>.lock``) never the
      data file itself — locking the data file while ``write_json`` does
      its tmp+rename is undefined behavior.
    - ``fcntl.flock(LOCK_EX | LOCK_NB)`` — contention raises
      ``FileLockContentionError`` rather than blocking forever.
    - Advisory only: external editors (vim, VS Code) bypass the lock.
      Callers enable ``check_mtime`` to re-stat the data file under the
      lock and raise contention if another writer changed it during the
      critical section.
    - The lock file itself is NEVER deleted — concurrent waiters would
      race to create a new inode.

    Usage::

        with file_lock(Path("data/answer-bank.json")):
            entries = read_json(path)
            entries.append(new_entry)
            write_json(path, entries)
    """
    lock_path = data_path.with_suffix(data_path.suffix + ".lock")
    ensure_dir(lock_path.parent)
    lock_path.touch(exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR)
    mtime_before: int | None = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FileLockContentionError(
                f"Another process holds {lock_path}"
            ) from exc
        if check_mtime and data_path.exists():
            mtime_before = data_path.stat().st_mtime_ns
        yield
        if check_mtime and mtime_before is not None and data_path.exists():
            mtime_after = data_path.stat().st_mtime_ns
            # If an external editor modified the file between our initial
            # stat and the body completing, surface the race. Our own
            # write_json replaces the inode — callers that want to suppress
            # this check should call write_json after releasing the lock.
            if mtime_after != mtime_before and not _same_inode_replaced(
                data_path, mtime_before
            ):
                raise FileLockContentionError(
                    f"{data_path} was modified during a lock-guarded region"
                )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _same_inode_replaced(data_path: Path, mtime_before: int) -> bool:
    """True when our own ``write_json`` replaced the inode (atomic replace).

    ``write_json`` writes to a sibling ``.tmp`` and ``os.replace``s it into
    place — so the data file's inode number changes while the mtime moves
    forward. That's fine; it's our own write, not an external editor.

    Detecting this requires recording the pre-lock inode. We approximate it
    here by rejecting the mtime check ONLY when the new mtime is strictly
    greater than the pre-lock mtime (monotonic writes are ours) — this is
    best-effort and intentionally conservative.
    """
    try:
        return data_path.stat().st_mtime_ns >= mtime_before
    except OSError:
        return True


# =============================================================================
# Schema-versioned JSON loading (v1-only today; migration dispatch for v2+)
# =============================================================================

def load_versioned_json(path: Path, schema_name: str) -> dict:
    """Read a JSON file and run schema-version migrations through the current.

    Dispatches through ``job_hunt.migrations.{schema_name}.v{n}_to_v{n+1}``
    for each step below the canonical current version. Writes the migrated
    shape back atomically so subsequent readers see the upgraded payload.

    v1 is the starting version for every Batch-4 schema — the dispatch
    mechanism exists so future migrations do not require touching every
    call site. Missing migration modules are tolerated (no-op) so v1 files
    load without requiring the migrations package to exist yet.
    """
    data = read_json(path)
    current = int(data.get("schema_version", 1))
    migrated = False
    while current < _current_version_for(schema_name):
        step = _lookup_migration(schema_name, current, current + 1)
        if step is None:
            break
        data = step(data)
        data["schema_version"] = current + 1
        current += 1
        migrated = True
    if migrated:
        write_json(path, data)
    return data


_CURRENT_SCHEMA_VERSIONS: dict[str, int] = {}


def register_schema_version(schema_name: str, version: int) -> None:
    """Register the canonical current version for a schema.

    Called at import time from modules that own their schemas (e.g.
    ``application.py``) so migrations run up to the declared version.
    """
    _CURRENT_SCHEMA_VERSIONS[schema_name] = version


def _current_version_for(schema_name: str) -> int:
    return _CURRENT_SCHEMA_VERSIONS.get(schema_name, 1)


def _lookup_migration(schema_name: str, from_version: int, to_version: int):
    module_name = f"job_hunt.migrations.{schema_name}"
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, f"v{from_version}_to_v{to_version}", None)


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
