"""Playwright browser lifecycle + robust page-readiness signals.

Readiness is captured as data (which signals fired, and their timings) rather
than hidden behind fixed sleeps, so a reader of `page.json` can judge whether
the snapshot was taken against a settled page.
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


def navigate(page: Page, url: str, timeout_ms: int = 30000) -> dict[str, Any]:
    """Navigate to `url` and wait for the page to settle. Returns a readiness
    report; never raises on the soft waits (networkidle / body)."""
    signals: dict[str, Any] = {"requested_url": url}
    t0 = time.monotonic()

    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    signals["dom_content_loaded_ms"] = round((time.monotonic() - t0) * 1000)
    signals["http_status"] = response.status if response is not None else None

    # Soft wait: let XHR/fetch settle (SPAs). Don't fail if it never idles.
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
        signals["networkidle"] = True
    except PlaywrightTimeoutError:
        signals["networkidle"] = False

    # Soft wait: ensure a body exists to extract from. Use state="attached"
    # (not the default "visible") — an empty <body> has zero size and would
    # otherwise never count as "visible".
    try:
        page.wait_for_selector("body", state="attached", timeout=3000)
        signals["body_present"] = True
    except PlaywrightTimeoutError:
        signals["body_present"] = False

    signals["total_wait_ms"] = round((time.monotonic() - t0) * 1000)
    return signals


def aria_snapshot(page: Page) -> str | None:
    """The browser's own ARIA snapshot (YAML) for the document body.

    This is Playwright's current accessibility-tree API. Kept alongside the
    deterministic per-element pass, not instead of it.
    """
    try:
        return page.locator("body").aria_snapshot()
    except Exception:
        return None
