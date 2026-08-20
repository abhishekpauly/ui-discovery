"""Two things that used to need the operator to know about them.

An app holding a websocket open never reaches `networkidle`, so the DOM
plateau carries the whole argument — and a pause between render bursts looks
exactly like being finished. That previously required hand-writing an
`extra_wait` adapter into the config, which only helps if you already know
the app behaves that way.

And a saved session that has already lapsed should cost a second to discover,
not a full crawl of login screens.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ui_discovery.auth import describe_session, session_status
from ui_discovery.extraction import extract_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


# --- held-open connections ---------------------------------------------------

def test_a_held_open_connection_is_detected():
    page = extract_page(_url("edge/streaming.html"))
    assert page.readiness["held_open_connection"] is True


def test_content_after_a_gap_is_still_captured():
    """The second burst arrives 1.9s in, well past the default window. This
    is what the `extra_wait` adapter used to be needed for."""
    page = extract_page(_url("edge/streaming.html"))
    names = {e.accessible_name for e in page.elements if e.accessible_name}
    assert "Early" in names
    assert "Late arrival" in names, f"late content missed; got {sorted(names)}"


def test_an_ordinary_page_does_not_pay_the_streaming_cost():
    """The strict profile must apply only where it is earned — a normal page
    should not wait seconds longer for nothing."""
    page = extract_page(_url("static.html"))
    assert page.readiness.get("held_open_connection") is False
    assert page.readiness["dom_stable_wait_ms"] < 3000


# --- session pre-flight ------------------------------------------------------

def _jwt(exp: float) -> str:
    import base64
    import json

    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _state(exp: float, origin: str = "https://app.test") -> dict:
    return {"cookies": [], "origins": [
        {"origin": origin, "localStorage": [
            {"name": "accessToken", "value": _jwt(exp)}]}]}


def test_a_live_session_is_reported_as_live():
    status = session_status(_state(time.time() + 7200), "https://app.test/x")
    assert status["known"] and not status["expired"]
    assert 3500 < status["seconds_remaining"] < 7300


def test_an_expired_session_is_caught_before_the_crawl():
    status = session_status(_state(time.time() - 60), "https://app.test/x")
    assert status["expired"] is True
    lines = describe_session(_state(time.time() - 60), "s.json", "https://app.test/x")
    assert any("expired" in ln for ln in lines)
    assert any("ui_discovery.login" in ln for ln in lines)


def test_another_providers_expired_cookie_does_not_condemn_the_session():
    """Regression: logging in via Google leaves that provider's cookies in the
    same storage state. Taking the earliest expiry across all of them declared
    a working session dead — its own token had 16 hours left."""
    state = _state(time.time() + 7200)
    state["cookies"] = [{
        "name": "__Secure-1PSIDRTS", "domain": ".google.com",
        "expires": time.time() - 86400,
    }]
    status = session_status(state, "https://app.test/x")
    assert status["expired"] is False
    assert "app.test" in status["source"]


def test_a_session_for_a_different_origin_is_not_consulted():
    status = session_status(_state(time.time() - 60, "https://other.test"),
                            "https://app.test/x")
    assert status["known"] is False


def test_a_cookie_only_session_reports_unknown_rather_than_guessing():
    """Which cookie carries the session is not knowable from disk. The honest
    answer is "unknown"; the crawl's own check remains the backstop."""
    state = {"cookies": [{"name": "sid", "domain": "app.test",
                          "expires": time.time() + 3600}], "origins": []}
    assert session_status(state, "https://app.test/x")["known"] is False
    assert any("no expiry we can read" in ln
               for ln in describe_session(state, "s.json", "https://app.test/x"))


def test_no_session_says_nothing():
    assert describe_session(None, None) == []
    assert session_status(None) == {"known": False}
