from __future__ import annotations

from .base import CLAUDE_CHROME


def describe_backend() -> dict[str, object]:
    return {
        "name": CLAUDE_CHROME.name,
        "supports_browser_automation": CLAUDE_CHROME.supports_browser_automation,
        "notes": CLAUDE_CHROME.notes,
    }

