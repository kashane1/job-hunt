from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutorBackend:
    name: str
    supports_browser_automation: bool
    notes: str = ""


CLAUDE_CHROME = ExecutorBackend(
    name="claude_chrome",
    supports_browser_automation=True,
    notes="Current primary backend for automation playbooks.",
)

CODEX_BROWSER = ExecutorBackend(
    name="codex_browser",
    supports_browser_automation=True,
    notes="Reserved seam for future Codex-browser runtime integration.",
)

NONE = ExecutorBackend(
    name="none",
    supports_browser_automation=False,
    notes="Manual-assist only; no browser automation permitted.",
)

