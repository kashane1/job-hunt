from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.executors.base import CLAUDE_CHROME, NONE
from job_hunt.executors.registry import get_executor


class ExecutorRegistryTest(unittest.TestCase):
    def test_executor_capabilities_are_typed(self) -> None:
        chrome = get_executor("claude_chrome")
        self.assertTrue(chrome.capabilities.browser_automation)
        self.assertTrue(chrome.capabilities.file_upload)
        self.assertTrue(chrome.supports_browser_automation)

    def test_manual_assist_backend_disables_browser_automation(self) -> None:
        backend = get_executor("none")
        self.assertFalse(backend.capabilities.browser_automation)
        self.assertFalse(backend.supports_browser_automation)

    def test_unknown_backend_falls_back_to_primary(self) -> None:
        self.assertEqual(get_executor("does-not-exist").name, CLAUDE_CHROME.name)
        self.assertEqual(NONE.name, "none")


if __name__ == "__main__":
    unittest.main()
