"""L1 — every screen states whether it is the screen it claims to be.

`Page.requested_url` and `Page.final_url` have both existed since V0, and
until L1 no report compared them. The failure that made the case: a real
capture of a portal's control-center route landed on that product's dashboard,
and `summary.md` reported one screen, named it with the URL that had been
asked for, filed a screenshot of the dashboard under that name, and said
nothing. Every fact needed to catch it was already in the model.

The tests are in three layers, deliberately: the judgement is pure and tested
without a browser, the rendering is tested from synthetic crawls, and only the
end-to-end claims pay for Chromium.
"""

from __future__ import annotations

import asyncio
import http.server
import socket
import threading

import pytest

from ui_discovery.crawler import crawl_site
from ui_discovery.extraction import capture_verdict
from ui_discovery.inventory import _summary_markdown, build_inventory
from ui_discovery.models import (
    AuthCheck,
    Crawl,
    CrawlConfig,
    CrawlStats,
    Element,
    Heading,
    Page,
    PageNode,
)
from ui_discovery.reports import build_html

# --- helpers ----------------------------------------------------------------


def _page(
    *,
    requested="https://x.test/orders",
    final=None,
    elements=1,
    headings=1,
    auth=None,
    readiness=None,
    title="Orders",
) -> Page:
    return Page(
        schema_version="0.1.0",
        engine_version="0",
        extracted_at="",
        requested_url=requested,
        final_url=final if final is not None else requested,
        title=title,
        readiness=readiness if readiness is not None else {"http_status": 200},
        headings=[Heading(level=1, text="Orders") for _ in range(headings)],
        elements=[Element(category="link", tag="a") for _ in range(elements)],
        auth=auth,
    )


# --- the judgement, pure ----------------------------------------------------


def test_a_page_that_is_where_it_asked_to_be_is_captured():
    v = capture_verdict(_page())
    assert v.verdict == "captured"
    assert v.redirected_to is None
    assert v.element_count == 1


def test_a_different_final_url_is_redirected_and_carries_both():
    v = capture_verdict(_page(
        requested="https://x.test/control-center",
        final="https://x.test/dashboard",
    ))
    assert v.verdict == "redirected"
    assert v.requested_url == "https://x.test/control-center"
    assert v.final_url == "https://x.test/dashboard"
    assert v.redirected_to == "https://x.test/dashboard"


def test_a_trailing_slash_is_not_a_redirect():
    # `/settings` -> `/settings/` is not a navigation, and reporting it as one
    # would bury the redirects that matter under noise. Same definition of
    # "the same page" the crawl uses for identity, so the two cannot drift.
    v = capture_verdict(_page(
        requested="https://x.test/settings",
        final="https://x.test/settings/",
    ))
    assert v.verdict == "captured"


def test_a_fragment_is_not_a_redirect():
    v = capture_verdict(_page(
        requested="https://x.test/orders",
        final="https://x.test/orders#top",
    ))
    assert v.verdict == "captured"


def test_a_login_page_is_an_auth_wall_even_though_it_also_redirected():
    # Both facts are true; "your session is gone" is the one to act on, and
    # the redirect is still recorded rather than lost to the more severe
    # verdict.
    v = capture_verdict(_page(
        requested="https://x.test/orders",
        final="https://x.test/login",
        auth=AuthCheck(looks_logged_out=True, signal="login-url",
                       evidence="https://x.test/login"),
    ))
    assert v.verdict == "auth_wall"
    assert v.reason == "auth:login-url"
    assert v.redirected_to == "https://x.test/login"


def test_a_page_that_rendered_nothing_is_empty():
    v = capture_verdict(_page(
        elements=0, headings=0,
        auth=AuthCheck(looks_empty=True, signal="empty-page"),
    ))
    assert v.verdict == "empty"


def test_an_http_error_outranks_everything_below_it():
    v = capture_verdict(_page(readiness={"http_status": 503}))
    assert v.verdict == "error"
    assert v.reason == "http-status:503"
    assert v.http_status == 503


def test_an_explicit_status_argument_wins_over_readiness():
    v = capture_verdict(_page(readiness={}), http_status=404)
    assert v.verdict == "error"


def test_a_page_that_never_settled_is_unknown_not_empty():
    # The verdict this vocabulary has `unknown` for: a page still loading and
    # a page that finished with nothing on it look identical, and guessing
    # between them is the confident-wrong answer.
    v = capture_verdict(_page(
        elements=0, headings=0,
        readiness={"http_status": 200, "body_present": False},
    ))
    assert v.verdict == "unknown"
    assert v.reason == "page-never-settled"


def test_no_content_and_no_settle_evidence_is_unknown():
    v = capture_verdict(_page(elements=0, headings=0, readiness={}))
    assert v.verdict == "unknown"


def test_a_missing_status_does_not_become_an_error():
    v = capture_verdict(_page(readiness={}))
    assert v.verdict == "captured"
    assert v.http_status is None


# --- the rollup and the rendering -------------------------------------------


def _crawl(verdicts: list[str]) -> Crawl:
    """A crawl whose pages carry exactly the verdicts named."""
    nodes = []
    for i, name in enumerate(verdicts):
        url = f"https://x.test/{i}"
        if name == "captured":
            page = _page(requested=url)
        elif name == "redirected":
            page = _page(requested=url, final="https://x.test/dashboard")
        elif name == "auth_wall":
            page = _page(requested=url, final="https://x.test/login",
                         auth=AuthCheck(looks_logged_out=True,
                                        signal="login-url"))
        elif name == "empty":
            page = _page(requested=url, elements=0, headings=0,
                         auth=AuthCheck(looks_empty=True, signal="empty-page"))
        else:
            page = _page(requested=url, readiness={"http_status": 500})
        page.verdict = capture_verdict(page)
        assert page.verdict.verdict == name, page.verdict
        nodes.append(PageNode(url=url, page=page, depth=0))
    counts: dict[str, int] = {}
    for name in verdicts:
        counts[name] = counts.get(name, 0) + 1
    return Crawl(
        schema_version="0.1.0", engine_version="0", crawl_id="c",
        started_at="", finished_at="",
        config=CrawlConfig(start_url="https://x.test/0", max_pages=10,
                           max_depth=2, strategy="same-domain"),
        stats=CrawlStats(pages_crawled=len(nodes), pages_failed=0,
                         unique_urls=len(nodes), links_discovered=0,
                         runtime_seconds=1.0, verdicts=counts),
        pages=nodes,
    )


def test_inventory_counts_by_verdict():
    inv = build_inventory(_crawl(["captured", "captured", "redirected"]))
    assert inv["verdicts"] == {"captured": 2, "redirected": 1}
    assert inv["screens_captured"] == 2
    assert inv["screens"][2]["redirected_to"] == "https://x.test/dashboard"


def test_a_healthy_capture_says_nothing_extra():
    # Crying wolf on a clean capture is how the banner stops being read.
    md = _summary_markdown(build_inventory(_crawl(["captured", "captured"])))
    assert "claimed to be" not in md
    assert "🛑" not in md


def test_a_mostly_wrong_capture_leads_with_it():
    md = _summary_markdown(build_inventory(
        _crawl(["auth_wall", "auth_wall", "auth_wall", "captured"])))
    head = md.split("## Elements by kind")[0]
    assert "🛑" in head
    assert "mostly not of the product" in head
    assert "1 of 4" in head
    assert "3 auth wall" in head
    # The headline count must not stand unqualified next to it.
    assert "1 of 4 were the screen they claimed to be" in head


def test_a_single_bad_screen_is_a_note_not_an_alarm():
    md = _summary_markdown(build_inventory(
        _crawl(["captured", "captured", "captured", "redirected"])))
    head = md.split("## Elements by kind")[0]
    assert "🛑" not in head
    assert "1 screen(s) were not the screen they claimed to be" in head


def test_the_screens_table_names_the_destination():
    md = _summary_markdown(build_inventory(_crawl(["captured", "redirected"])))
    rows = [ln for ln in md.splitlines() if ln.startswith("| 2 |")]
    assert rows and "redirected" in rows[0]
    assert "https://x.test/dashboard" in rows[0]


def test_the_html_report_banners_and_states_each_screen():
    html = build_html(_crawl(["captured", "redirected"]))
    assert "were the screen they claimed to be" in html
    assert "Verdict: redirected" in html
    assert "https://x.test/dashboard" in html


# --- end to end -------------------------------------------------------------

PROTECTED = (b"<!doctype html><title>Dashboard</title><body><main>"
             b"<h1>Secret Dashboard</h1><a href='/gated-next'>Next</a>"
             b"</main></body>")
LOGIN = (b"<!doctype html><title>Sign in</title><body><main>"
         b"<h1>Sign in to continue</h1>"
         b"<form><input type='text' name='user' aria-label='User'>"
         b"<input type='password' name='pw' aria-label='Password'>"
         b"</form></main></body>")
MOVED = (b"<!doctype html><title>Real Home</title><body><main>"
         b"<h1>Real Home</h1><a href='/stay'>Stay</a></main></body>")
STAY = (b"<!doctype html><title>Stay</title><body><main>"
        b"<h1>Stay</h1></main></body>")


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves three shapes at once: a cookie gate, a 302, and a plain page."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/moved"):
            self.send_response(302)
            self.send_header("Location", "/home")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/gated"):
            authed = "ui_session=valid" in self.headers.get("Cookie", "")
            body = PROTECTED if authed else LOGIN
        elif self.path.startswith("/stay"):
            body = STAY
        else:
            body = MOVED
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


def _crawl_sync(url, **kw):
    return asyncio.run(crawl_site(url, **kw))


def test_a_302_is_reported_as_redirected_with_both_urls(server, tmp_path):
    crawl = _crawl_sync(f"{server}/moved", output_dir=str(tmp_path),
                        max_pages=1, max_depth=0, screenshots=False,
                        probe=False)
    verdict = crawl.pages[0].page.verdict
    assert verdict.verdict == "redirected"
    assert verdict.requested_url.endswith("/moved")
    assert verdict.final_url.endswith("/home")
    assert crawl.stats.verdicts == {"redirected": 1}


def test_an_ordinary_page_is_captured(server, tmp_path):
    crawl = _crawl_sync(f"{server}/stay", output_dir=str(tmp_path),
                        max_pages=1, max_depth=0, screenshots=False,
                        probe=False)
    assert crawl.pages[0].page.verdict.verdict == "captured"
    assert crawl.stats.verdicts == {"captured": 1}


def test_the_cookie_gate_with_no_session_is_all_auth_wall(server, tmp_path):
    crawl = _crawl_sync(f"{server}/gated", output_dir=str(tmp_path),
                        max_pages=3, max_depth=1, screenshots=False,
                        probe=False)
    assert crawl.pages
    for node in crawl.pages:
        assert node.page.verdict.verdict == "auth_wall", node.url
        # The evidence is the login page itself, not a bare assertion.
        assert node.page.verdict.reason.startswith("auth:")
    assert set(crawl.stats.verdicts) == {"auth_wall"}
    md = _summary_markdown(build_inventory(crawl))
    assert "mostly not of the product" in md.split("## Elements by kind")[0]


def test_a_capture_from_before_l1_is_not_reported_as_broken():
    """A snapshot with no verdicts must re-render silently.

    "We did not ask" and "we asked and could not tell" are different facts.
    Defaulting the first to `unknown` would flip every capture taken before
    L1 to the stop-sign banner the moment its reports were regenerated.
    """
    crawl = _crawl(["captured", "captured"])
    for node in crawl.pages:
        node.page.verdict = None
    crawl.stats.verdicts = {}
    inv = build_inventory(crawl)
    assert inv["verdicts"] == {}
    md = _summary_markdown(inv)
    assert "claimed to be" not in md
    assert "🛑" not in md
    # The column still exists; it just has nothing to say.
    assert "| — |" in md
