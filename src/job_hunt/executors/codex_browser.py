from __future__ import annotations

from .base import CODEX_BROWSER


def describe_backend() -> dict[str, object]:
    return {
        "name": CODEX_BROWSER.name,
        "supports_browser_automation": CODEX_BROWSER.supports_browser_automation,
        "notes": CODEX_BROWSER.notes,
    }
