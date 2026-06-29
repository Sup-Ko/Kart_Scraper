"""Thin helper for fetching JS-heavy / anti-bot pages with Playwright.

Kept separate so sources that only need plain HTTP never import Playwright, and
so a missing/blocked browser degrades to an empty result instead of a crash.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from ..config import USER_AGENT

log = logging.getLogger(__name__)


@contextmanager
def browser_page(timeout_ms: int = 30000) -> Iterator[Optional["object"]]:
    """Yield a ready-to-use Playwright page, or ``None`` if unavailable.

    The Chromium binary ships pre-installed in this environment
    (``PLAYWRIGHT_BROWSERS_PATH``); there is no need to run ``playwright
    install``. Any launch failure is logged and yields ``None`` so callers can
    simply ``if page is None: return []``.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        log.warning("Playwright not available: %s", exc)
        yield None
        return

    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="fr-FR",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        yield page
    except Exception as exc:  # noqa: BLE001
        log.warning("Browser launch/navigation failed: %s", exc)
        yield None
    finally:
        try:
            if browser is not None:
                browser.close()
        finally:
            if pw is not None:
                pw.stop()
