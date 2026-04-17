"""Phase 1b tests for ``utils.file_lock`` + ``utils.load_versioned_json``.

Covers contention, mtime checks, and the migration-dispatch stub behavior.
``write_json`` concurrency invariants are already covered by
``test_foundation.WriteJsonConcurrentTest``; F_FULLFSYNC behavior is
platform-specific (no-op on non-Darwin) so we mock the call.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.utils import (
    FileLockContentionError,
    _fullfsync_if_darwin,
    file_lock,
    load_versioned_json,
    register_schema_version,
    write_json,
)


class FileLockTest(unittest.TestCase):
    def test_non_contended_lock_runs_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer-bank.json"
            write_json(path, {"entries": []})
            ran = False
            with file_lock(path):
                ran = True
            self.assertTrue(ran)
            # Sibling lock file exists (advisory; never deleted).
            self.assertTrue((path.with_suffix(path.suffix + ".lock")).exists())

    def test_contention_raises_file_lock_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bank.json"
            write_json(path, {"entries": []})
            with file_lock(path):
                with self.assertRaises(FileLockContentionError):
                    with file_lock(path):
                        pass

    def test_lock_is_sibling_not_data_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            write_json(path, {"x": 1})
            with file_lock(path):
                lock = path.with_suffix(path.suffix + ".lock")
                self.assertTrue(lock.exists())
                self.assertNotEqual(path, lock)
                # Data file is still readable outside the lock region.
                self.assertEqual(path.read_text(encoding="utf-8").count("\"x\""), 1)

    def test_mtime_check_ignores_our_own_write_under_lock(self) -> None:
        # write_json under the lock should NOT trigger the mtime-change
        # defense — our own atomic replace is a legitimate write.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            write_json(path, {"v": 0})
            with file_lock(path):
                # Sleep 1ns-ish just to ensure mtime ticks.
                time.sleep(0.01)
                write_json(path, {"v": 1})
            # No raise → mtime check did not flag our write.


class LoadVersionedJsonTest(unittest.TestCase):
    def test_v1_passthrough_no_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            write_json(path, {"schema_version": 1, "x": 1})
            data = load_versioned_json(path, "application-plan")
            self.assertEqual(data["x"], 1)
            self.assertEqual(data["schema_version"], 1)

    def test_older_version_with_migration_stub(self) -> None:
        # Synthesize a migration module at runtime to drive the dispatch.
        import importlib.util
        import types

        module_name = "job_hunt.migrations.fake_schema_for_test"
        module = types.ModuleType(module_name)

        def v0_to_v1(data: dict) -> dict:
            data["migrated"] = True
            return data

        module.v0_to_v1 = v0_to_v1
        sys.modules[module_name] = module

        register_schema_version("fake_schema_for_test", 1)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            write_json(path, {"schema_version": 0, "x": 7})
            data = load_versioned_json(path, "fake_schema_for_test")
            self.assertEqual(data["schema_version"], 1)
            self.assertTrue(data["migrated"])
            # File was rewritten in-place with the migrated payload.
            from job_hunt.utils import read_json
            persisted = read_json(path)
            self.assertEqual(persisted["schema_version"], 1)


class FullFsyncTest(unittest.TestCase):
    def test_darwin_triggers_fcntl_fullfsync(self) -> None:
        import job_hunt.utils as u

        with patch.object(u, "_IS_DARWIN", True), patch.object(
            u, "_F_FULLFSYNC", 51
        ), patch.object(u.fcntl, "fcntl") as mock_fcntl:
            _fullfsync_if_darwin(42)
            mock_fcntl.assert_called_once_with(42, 51)

    def test_non_darwin_is_noop(self) -> None:
        import job_hunt.utils as u

        with patch.object(u, "_IS_DARWIN", False), patch.object(
            u.fcntl, "fcntl"
        ) as mock_fcntl:
            _fullfsync_if_darwin(42)
            mock_fcntl.assert_not_called()

    def test_kernel_without_support_is_silently_ignored(self) -> None:
        import job_hunt.utils as u

        def boom(*a, **kw):
            raise OSError(22, "Invalid argument")

        with patch.object(u, "_IS_DARWIN", True), patch.object(
            u, "_F_FULLFSYNC", 51
        ), patch.object(u.fcntl, "fcntl", side_effect=boom):
            # Must not raise — fsync already ran; F_FULLFSYNC is best-effort.
            _fullfsync_if_darwin(42)


class ProfileCompletenessBackCompatTest(unittest.TestCase):
    """Regression guard for the profile.py extraction.

    A profile JSON written before Batch 4 will NOT have work_authorization
    or sponsorship_required keys. check_profile_completeness must not raise
    KeyError — it records the missing check as a simple miss.
    """

    def test_old_profile_without_work_auth_does_not_crash(self) -> None:
        from job_hunt.profile import check_profile_completeness

        old_shape = {
            "contact": {"emails": ["a@b.com"], "phones": ["555"], "links": ["https://linkedin.com/in/x"]},
            "documents": [{"document_type": "resume"}, {"document_type": "preferences"}, {"document_type": "project_note"}],
            "skills": [{"name": f"s{i}"} for i in range(6)],
            "question_bank": [{"question": "q"} for _ in range(3)],
            "preferences": {
                "target_titles": ["x"],
                "preferred_locations": ["Remote"],
                "remote_preference": "remote",
                "minimum_compensation": "x",
                "excluded_keywords": ["x"],
            },
            "experience_highlights": [
                {"summary": "shipped 10 things"} for _ in range(3)
            ],
        }
        report = check_profile_completeness(old_shape, {})
        self.assertLess(report["completeness_score"], 100)
        self.assertIn("Work authorization status", report["missing"])


if __name__ == "__main__":
    unittest.main()
