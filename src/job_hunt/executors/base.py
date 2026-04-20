from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutorCapabilities:
    browser_automation: bool
    file_upload: bool
    tab_management: bool
    checkpoint_resume: bool
    auth_session_reuse: bool
    screenshot_capture: bool
    dom_read: bool


@dataclass(frozen=True)
class ExecutorBackend:
    name: str
    capabilities: ExecutorCapabilities
    notes: str = ""

    @property
    def supports_browser_automation(self) -> bool:
        return self.capabilities.browser_automation


CLAUDE_CHROME = ExecutorBackend(
    name="claude_chrome",
    capabilities=ExecutorCapabilities(
        browser_automation=True,
        file_upload=True,
        tab_management=True,
        checkpoint_resume=True,
        auth_session_reuse=True,
        screenshot_capture=True,
        dom_read=True,
    ),
    notes="Current primary backend for automation playbooks.",
)

CODEX_BROWSER = ExecutorBackend(
    name="codex_browser",
    capabilities=ExecutorCapabilities(
        browser_automation=True,
        file_upload=True,
        tab_management=True,
        checkpoint_resume=True,
        auth_session_reuse=True,
        screenshot_capture=True,
        dom_read=True,
    ),
    notes="Reserved seam for future Codex-browser runtime integration.",
)

NONE = ExecutorBackend(
    name="none",
    capabilities=ExecutorCapabilities(
        browser_automation=False,
        file_upload=False,
        tab_management=False,
        checkpoint_resume=True,
        auth_session_reuse=False,
        screenshot_capture=False,
        dom_read=False,
    ),
    notes="Manual-assist only; no browser automation permitted.",
)
