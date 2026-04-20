from __future__ import annotations

from .base import CLAUDE_CHROME, CODEX_BROWSER, NONE, ExecutorBackend

_EXECUTORS = {
    CLAUDE_CHROME.name: CLAUDE_CHROME,
    CODEX_BROWSER.name: CODEX_BROWSER,
    NONE.name: NONE,
}


def get_executor(name: str) -> ExecutorBackend:
    return _EXECUTORS.get(name, CLAUDE_CHROME)

