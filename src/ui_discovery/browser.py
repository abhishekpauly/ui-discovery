"""Playwright browser lifecycle + robust page-readiness signals.

Readiness is captured as data (which signals fired, and their timings) rather
than hidden behind fixed sleeps, so a reader of `page.json` can judge whether
the snapshot was taken against a settled page.
"""

from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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



# Apps that hold a connection open never reach `networkidle` — a websocket or
# an SSE stream keeps traffic flowing forever. That is not a stalled page, but
# it does mean the network signal is useless there, leaving DOM-plateau
# detection to do all the work alone. It is not reliable alone: a pause
# between fetches looks exactly like being finished.
#
# So detect the condition rather than asking the operator to know about it.
# Wrapping the constructors catches both connected and attempted sockets,
# needs no cooperation from the page, and works identically in the sync and
# async paths.
LIVE_CONNECTION_PROBE_JS = """
(() => {
  if (window.__uid_conn) return;
  window.__uid_conn = { ws: 0, sse: 0 };
  const WS = window.WebSocket;
  if (WS) {
    window.WebSocket = function (...args) {
      window.__uid_conn.ws++;
      return new WS(...args);
    };
    window.WebSocket.prototype = WS.prototype;
    Object.assign(window.WebSocket, WS);
  }
  const ES = window.EventSource;
  if (ES) {
    window.EventSource = function (...args) {
      window.__uid_conn.sse++;
      return new ES(...args);
    };
    window.EventSource.prototype = ES.prototype;
    Object.assign(window.EventSource, ES);
  }
})();
"""

READ_LIVE_CONNECTIONS_JS = (
    "() => window.__uid_conn "
    "? window.__uid_conn.ws + window.__uid_conn.sse : 0"
)

# How long a page holding a connection open must stay unchanged before we
# believe it. Deliberately much stricter than the default: this is the case
# where the network signal tells us nothing, so the DOM has to carry the
# whole argument.
STREAMING_STABLE_POLLS = 6
STREAMING_TIMEOUT_MS = 20000


def wait_for_dom_stable(
    page: Page,
    *,
    networkidle: bool = True,
    live_connections: int = 0,
    timeout_ms: int = 8000,
    interval_ms: int = 250,
    required_stable_polls: int = 2,
) -> dict[str, Any]:
    """Poll `DOM_FINGERPRINT_JS` until the DOM stops changing *with content in
    it*, or `timeout_ms` elapses.

    A page whose network never went idle is still fetching, so a 500ms lull
    in the DOM means very little — under load, pages render in bursts with
    gaps longer than that. When `networkidle` did not fire we therefore
    demand a longer stretch of quiet before calling it settled.

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
    if not networkidle:
        # Still fetching: require a full second of quiet, and allow longer.
        required_stable_polls = max(required_stable_polls, 4)
        timeout_ms = max(timeout_ms, 15000)
    # A held-open connection is re-checked every poll rather than decided
    # here: the socket usually opens a second or two into page load, so
    # sampling once up front races it and reads zero on a page that is about
    # to hold one open for the rest of its life.
    strict = False

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
        if not strict:
            try:
                if int(page.evaluate(READ_LIVE_CONNECTIONS_JS) or 0):
                    strict = True
                    required_stable_polls = max(
                        required_stable_polls, STREAMING_STABLE_POLLS)
                    deadline = max(deadline, t0 + STREAMING_TIMEOUT_MS / 1000)
            except Exception:
                pass
        if fp == last and has_rendered(fp):
            stable_polls += 1
            if stable_polls >= required_stable_polls:
                return {
                    "dom_stable": True,
                    "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
                    "held_open_connection": strict,
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
        "held_open_connection": strict,
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
        signals.update(wait_for_dom_stable(
            page, networkidle=signals["networkidle"]))
    else:
        signals["dom_stable"] = False
        signals["dom_stable_wait_ms"] = 0

    signals["total_wait_ms"] = round((time.monotonic() - t0) * 1000)
    return signals


# Roles whose rendered "value" in an ARIA snapshot is text a person typed.
# Playwright renders it inline — `- textbox "API token": hunter2` — which put
# passwords and email addresses into every snapshot we have ever written.
_TYPED_VALUE_ROLES = ("textbox", "searchbox")
_TYPED_VALUE_LINE = re.compile(
    r'^(\s*-\s+(?:' + "|".join(_TYPED_VALUE_ROLES) + r')\b[^:]*):\s*\S.*$'
)


def redact_aria_snapshot(tree: str | None) -> str | None:
    """Strip typed text out of an ARIA snapshot, keeping its structure.

    The tree is worth having; what someone typed into a field is not ours to
    keep (CLAUDE.md: never persist secrets). The line keeps its role and
    accessible name and loses only the value, so the shape of the page — and
    the fact that the field exists — is unchanged.
    """
    if not tree:
        return tree
    return "\n".join(
        _TYPED_VALUE_LINE.sub(r"\1:", line) for line in tree.splitlines()
    )


def describe_redaction() -> dict:
    """G3: what the ARIA snapshot refuses to carry.

    Beside `redact_aria_snapshot` rather than in the manifest builder, so the
    roles named here are the roles actually stripped — `_TYPED_VALUE_ROLES` is
    read, not restated.
    """
    return {
        "redactions": [
            {
                "rule": "aria.typed_values",
                "applies_to": "the ARIA snapshot on every page",
                # The illustration uses a placeholder rather than a
                # password-shaped literal: this string ends up in every
                # manifest, and a capture that contains something looking like
                # a credential is exactly what a scan of these folders is for.
                "detail": (
                    "Playwright renders typed text inline "
                    '(`- textbox "API token": <what the user typed>`); the '
                    f"value is stripped for {' and '.join(_TYPED_VALUE_ROLES)} "
                    "roles, keeping the role and accessible name so the page "
                    "shape is unchanged"),
            },
        ],
    }


def aria_snapshot(page: Page) -> str | None:
    """The browser's own ARIA snapshot (YAML) for the document body.

    This is Playwright's current accessibility-tree API. Kept alongside the
    deterministic per-element pass, not instead of it. Typed field values are
    redacted; see `redact_aria_snapshot`.
    """
    try:
        return redact_aria_snapshot(page.locator("body").aria_snapshot())
    except Exception:
        return None
