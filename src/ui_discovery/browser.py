"""Playwright browser lifecycle + robust page-readiness signals.

Readiness is captured as data (which signals fired, and their timings) rather
than hidden behind fixed sleeps, so a reader of `page.json` can judge whether
the snapshot was taken against a settled page.
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

# A cheap fingerprint of DOM shape (node count + serialized size). Polled to
# detect when client-side rendering has actually finished — `networkidle`
# only tells us XHR/fetch traffic stopped, which SPAs reach *before* they're
# done painting (e.g. still waiting on a websocket push, a timer-driven state
# update, or a CSS transition). Cheap on purpose: this runs every poll tick.
# Four fields: serialized size, node count, *rendered text* length, and
# interactive-element count. The first two detect change; the last two decide
# whether anything has actually rendered — markup length alone is not a
# content signal, since an app shell's `<script>` block can be kilobytes of
# it while the page shows nothing.
DOM_FINGERPRINT_JS = """
() => {
  const b = document.body;
  if (!b) return '';
  const interactive = document.querySelectorAll(
    'a[href],button,input,select,textarea,[role=button],[role=link],[role=tab]'
  ).length;
  return [
    b.innerHTML.length,
    document.querySelectorAll('*').length,
    (b.innerText || '').trim().length,
    interactive,
  ].join(':');
}
"""


# Enough rendered text to call a page "showing something". Deliberately low:
# this only has to clear an app shell, which renders none.
RENDERED_TEXT_FLOOR = 20


def has_rendered(fingerprint: str) -> bool:
    """True if the fingerprint shows a page that has actually rendered.

    Judged on *rendered text* and interactive elements, not markup size — an
    unrendered shell can carry kilobytes of inline script while displaying
    nothing at all, which is exactly the case this exists to catch.
    """
    try:
        _html, _nodes, text_len, interactive = (
            int(part) for part in fingerprint.split(":")
        )
    except (ValueError, AttributeError, TypeError):
        return False
    return text_len >= RENDERED_TEXT_FLOOR or interactive >= 1


def wait_for_dom_stable(
    page: Page,
    *,
    timeout_ms: int = 8000,
    interval_ms: int = 250,
    required_stable_polls: int = 2,
) -> dict[str, Any]:
    """Poll `DOM_FINGERPRINT_JS` until the DOM stops changing *with content in
    it*, or `timeout_ms` elapses.

    The "with content" part is not fussiness. An app shell that has not begun
    rendering produces an identical fingerprint on every poll, so a plain
    equality check calls it stable after two ticks — declaring a blank page
    settled precisely because nothing has happened yet. Observed live: a
    dashboard reported `dom_stable` after 550ms with an empty body, and every
    downstream stage then faithfully recorded a page with zero elements.

    So an empty body never satisfies stability; we keep polling until content
    appears or we run out of time. A page that is genuinely empty costs the
    full timeout and reports `dom_stable: false` — which is the honest answer,
    and is what the H4 empty-page check should be reacting to.
    """
    t0 = time.monotonic()
    deadline = t0 + timeout_ms / 1000
    last = None
    stable_polls = 0
    saw_content = False
    while time.monotonic() < deadline:
        try:
            fp = page.evaluate(DOM_FINGERPRINT_JS)
        except Exception:
            break
        saw_content = saw_content or has_rendered(fp)
        if fp == last and has_rendered(fp):
            stable_polls += 1
            if stable_polls >= required_stable_polls:
                return {
                    "dom_stable": True,
                    "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
                }
        else:
            stable_polls = 0
        last = fp
        page.wait_for_timeout(interval_ms)
    return {
        "dom_stable": False,
        "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
        # Distinguishes "never rendered anything" from "rendered but kept
        # changing" — very different problems with the same timeout.
        "dom_content_seen": saw_content,
    }


def navigate(page: Page, url: str, timeout_ms: int = 30000) -> dict[str, Any]:
    """Navigate to `url` and wait for the page to settle. Returns a readiness
    report; never raises on the soft waits (networkidle / body / DOM stable)."""
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

    # Soft wait: past networkidle, keep polling until the DOM stops mutating
    # — this is what actually protects extraction/screenshots from firing
    # mid-render on SPAs that finish painting after their network traffic
    # settles (websocket-driven state, timers, CSS transitions).
    if signals["body_present"]:
        signals.update(wait_for_dom_stable(page))
    else:
        signals["dom_stable"] = False
        signals["dom_stable_wait_ms"] = 0

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
