"""H4 — fail loudly when a saved session has expired.

The failure this prevents: a crawl presents a stale session, gets redirected
to the login page on every request, and cheerfully reports "42 pages
captured" — a capture of the login flow, not the product.

Also guards the localStorage half of session restore. Cookies alone are not a
session for token-in-localStorage SPAs, and that path has silently regressed
before, so it is pinned here.
"""

from __future__ import annotations

import asyncio
import http.server
import socket
import threading

import pytest

from ui_discovery.auth import check_auth
from ui_discovery.crawler import crawl_site
from ui_discovery.extraction import extract_page
from ui_discovery.models import AuthCheck, Element, Heading, Page

# A site that gates on a cookie: anonymous requests get the login page.
PROTECTED = (b"<!doctype html><title>Dashboard</title><body><main>"
             b"<h1>Secret Dashboard</h1><a href='/next'>Next</a></main></body>")
LOGIN = (b"<!doctype html><title>Sign in</title><body><main>"
         b"<h1>Sign in to continue</h1>"
         b"<form><input type='text' name='user' aria-label='User'>"
         b"<input type='password' name='pw' aria-label='Password'>"
         b"</form></main></body>")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        authed = "ui_session=valid" in self.headers.get("Cookie", "")
        body = PROTECTED if authed else LOGIN
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _valid_state() -> dict:
    return {
        "cookies": [{
            "name": "ui_session", "value": "valid",
            "domain": "127.0.0.1", "path": "/",
            "httpOnly": False, "secure": False, "sameSite": "Lax",
            "expires": -1,
        }],
        "origins": [],
    }


def _expired_state() -> dict:
    state = _valid_state()
    state["cookies"][0]["value"] = "stale"  # server no longer accepts it
    return state


# --- the detector, in isolation ---------------------------------------------

def _page(**kw) -> Page:
    base = dict(
        schema_version="0.1.0", engine_version="0", extracted_at="now",
        requested_url="http://x.test/app", final_url="http://x.test/app",
        title="Dashboard",
    )
    base.update(kw)
    return Page(**base)


def test_password_field_means_logged_out():
    page = _page(elements=[Element(
        category="input", tag="input", attributes={"type": "password"},
    )])
    assert check_auth(page).signal == "password-field"


def test_hidden_password_field_does_not_fire():
    # A hidden password field is common in "change password" flows that are
    # only reachable *when* signed in.
    page = _page(elements=[Element(
        category="input", tag="input", visible=False,
        attributes={"type": "password"},
    )])
    assert check_auth(page).looks_logged_out is False


def test_login_url_means_logged_out():
    page = _page(final_url="http://x.test/account/login?next=/app")
    assert check_auth(page).signal == "login-url"


def test_logged_out_title_and_heading():
    assert check_auth(_page(title="Sign in · Acme")).signal == "logged-out-title"
    assert check_auth(_page(
        title="Acme", headings=[Heading(level=1, text="Log in")],
    )).signal == "logged-out-heading"


def test_ordinary_page_is_not_flagged():
    page = _page(
        title="Dashboard",
        headings=[Heading(level=1, text="Welcome")],
        elements=[Element(category="button", tag="button")],
    )
    assert check_auth(page) == AuthCheck()


def test_settled_but_empty_page_is_flagged():
    # Observed live: an SPA whose token is rejected can render nothing at all
    # rather than redirect to a login page — a silent failed capture.
    page = _page(title="App", headings=[], elements=[],
                 readiness={"body_present": True})
    result = check_auth(page)
    assert result.looks_empty is True
    assert result.looks_logged_out is False
    assert result.signal == "empty-page"


def test_page_that_never_loaded_a_body_is_not_called_empty():
    # No body means the navigation failed, which is a different problem and
    # already visible in readiness — don't relabel it as an auth issue.
    page = _page(title="", headings=[], elements=[],
                 readiness={"body_present": False})
    assert check_auth(page).looks_empty is False


@pytest.mark.parametrize("title", [
    "Designing reports",   # contains "signin"
    "Campaign login rules",  # "login" here IS a real word boundary...
])
def test_titles_are_matched_on_word_boundaries(title):
    # A false positive reports a healthy crawl as an expired session, so the
    # matcher must not fire on words that merely spell a signal.
    # "Designing" must not match; "Campaign login rules" legitimately does.
    result = check_auth(_page(title=title, headings=[]))
    assert result.looks_logged_out is ("login" in title.lower().split()
                                       or "log in" in title.lower())


def test_login_url_requires_a_whole_path_segment():
    assert check_auth(_page(final_url="http://x.test/login")).looks_logged_out
    assert check_auth(_page(final_url="http://x.test/app/login/")).looks_logged_out
    # A path that merely starts with the word is not a login page.
    assert not check_auth(
        _page(final_url="http://x.test/logingroups", title="Groups")
    ).looks_logged_out


# --- end to end -------------------------------------------------------------

def test_valid_session_is_not_flagged(server):
    page = extract_page(f"{server}/dashboard", auth_state=_valid_state())
    assert page.auth.looks_logged_out is False


def test_expired_session_is_detected(server):
    page = extract_page(f"{server}/dashboard", auth_state=_expired_state())
    assert page.auth.looks_logged_out is True


def test_crawl_marks_auth_expired_when_session_is_stale(server):
    crawl = asyncio.run(crawl_site(
        f"{server}/dashboard", max_depth=1, max_pages=2,
        output_dir="/tmp/uidisco_h4_expired", auth_state=_expired_state(),
    ))
    assert crawl.config.auth_used is True
    assert crawl.stats.pages_logged_out > 0
    assert crawl.stats.auth_expired is True


def test_crawl_with_valid_session_is_clean(server):
    crawl = asyncio.run(crawl_site(
        f"{server}/dashboard", max_depth=1, max_pages=2,
        output_dir="/tmp/uidisco_h4_valid", auth_state=_valid_state(),
    ))
    assert crawl.stats.auth_expired is False
    assert crawl.stats.pages_logged_out == 0


def test_no_session_supplied_is_not_an_expiry(server):
    # Landing on a login page without credentials is expected, not a failure.
    crawl = asyncio.run(crawl_site(
        f"{server}/dashboard", max_depth=1, max_pages=2,
        output_dir="/tmp/uidisco_h4_anon",
    ))
    assert crawl.config.auth_used is False
    assert crawl.stats.pages_logged_out > 0
    assert crawl.stats.auth_expired is False


def test_crawler_applies_localstorage_from_the_session(serve, tmp_path):
    """Regression guard: cookies are not a session for token-in-localStorage
    SPAs, and the crawler restores localStorage through a hook that has
    silently regressed before. This pins it.
    """
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Token app</title><body><main>"
        "<h1 id='who'>anonymous</h1></main>"
        "<script>"
        "  var t = localStorage.getItem('accessToken');"
        "  if (t) document.getElementById('who').textContent = 'token:' + t;"
        "</script></body>",
        encoding="utf-8",
    )
    from tests.conftest import Server

    site = Server(tmp_path)
    try:
        state = {
            "cookies": [],
            "origins": [{
                "origin": site.base,
                "localStorage": [{"name": "accessToken", "value": "abc123"}],
            }],
        }
        crawl = asyncio.run(crawl_site(
            site.url("index.html"), max_depth=0, max_pages=1,
            output_dir=str(tmp_path / "out"), auth_state=state,
        ))
    finally:
        site.stop()

    headings = [h.text for n in crawl.pages for h in n.page.headings]
    assert "token:abc123" in headings, (
        f"localStorage was not applied before the page ran; got {headings}"
    )


def test_expiry_is_surfaced_in_the_reports(server):
    from ui_discovery.reports import build_html, build_markdown

    crawl = asyncio.run(crawl_site(
        f"{server}/dashboard", max_depth=1, max_pages=2,
        output_dir="/tmp/uidisco_h4_report", auth_state=_expired_state(),
    ))
    assert "Session rejected." in build_markdown(crawl)
    assert "Session rejected." in build_html(crawl)


# --- proportionate evidence (found by running against a real portal) --------
#
# On a real QA portal, three `agent-builder/<uuid>` deep links rendered
# blank — they need query params the crawler did not have. That flagged the
# whole capture as "the login/blank state, not the product" while thirty-five
# other screens held real content (median 47 elements). Telling someone to
# throw away a good capture is as bad as missing a bad one.

def _crawl_with(nodes_empty: int, nodes_total: int, *, logged_out: int = 0):
    """Build a Crawl whose stats mirror a capture with some blank pages."""
    from ui_discovery.models import (
        AuthCheck,
        Crawl,
        CrawlConfig,
        CrawlStats,
        Page,
        PageNode,
    )

    def node(i: int, empty: bool, out: bool):
        page = Page(
            schema_version="0.1.0", engine_version="0", extracted_at="",
            requested_url=f"https://x.test/{i}", final_url=f"https://x.test/{i}",
            title="", auth=AuthCheck(looks_empty=empty, looks_logged_out=out),
        )
        return PageNode(url=f"https://x.test/{i}", page=page)

    pages = []
    for i in range(nodes_total):
        pages.append(node(i, i < nodes_empty,
                          nodes_empty <= i < nodes_empty + logged_out))
    return Crawl(
        schema_version="0.1.0", engine_version="0", crawl_id="c",
        started_at="", finished_at="",
        config=CrawlConfig(start_url="https://x.test/", max_pages=nodes_total,
                           max_depth=1, strategy="same-domain", auth_used=True),
        stats=CrawlStats(pages_crawled=nodes_total, pages_failed=0,
                         unique_urls=nodes_total, links_discovered=0,
                         runtime_seconds=0.0),
        pages=pages,
    )


def _verdict(empty: int, total: int, logged_out: int = 0) -> bool:
    """The rule the crawler applies when assembling stats."""
    return logged_out > 0 or (empty > 0 and empty * 2 >= total)


def test_a_few_blank_pages_do_not_condemn_a_good_capture():
    assert _verdict(empty=3, total=38) is False


def test_a_mostly_blank_capture_is_still_flagged():
    assert _verdict(empty=19, total=38) is True
    assert _verdict(empty=1, total=1) is True


def test_one_login_page_while_holding_a_session_is_enough():
    """A login page reached *with* a session is unambiguous — unlike a blank
    page, which an SPA renders for plenty of non-auth reasons."""
    assert _verdict(empty=0, total=38, logged_out=1) is True


def test_the_report_does_not_cry_wolf_on_a_mostly_good_capture():
    from ui_discovery.reports import build_markdown

    crawl = _crawl_with(3, 38)
    crawl.stats.pages_empty = 3
    crawl.stats.auth_expired = _verdict(3, 38)
    md = build_markdown(crawl)
    assert "Session rejected." not in md
