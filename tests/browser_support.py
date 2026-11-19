"""Pick the browser for UI smoke tests: the shared Playwright container, else a local Chromium.

Set ``WINDSHIELD_PLAYWRIGHT_WS`` (see ``windshield/scripts/playwright_container.sh``) to run the
browser tests in the pinned container. Without it nothing changes: a local Chromium is used and
the tests skip when none is installed.
"""

from __future__ import annotations

import os
import shutil

import pytest

CHROMIUM = (
    shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")
)
REMOTE = bool(os.environ.get("WINDSHIELD_PLAYWRIGHT_WS", "").strip())

requires_browser = pytest.mark.skipif(
    CHROMIUM is None and not REMOTE,
    reason="no Chromium (and no WINDSHIELD_PLAYWRIGHT_WS container) for a browser test",
)


def launch_browser(playwright):
    """Return a headless Chromium ``Browser`` from the container or the local binary."""
    if REMOTE:
        pytest.importorskip("windshield.remote")
        from windshield.remote import launch_or_connect

        local = {"executable_path": CHROMIUM} if CHROMIUM else {}
        return launch_or_connect(playwright, headless=True, **local)
    return playwright.chromium.launch(executable_path=CHROMIUM, headless=True)
