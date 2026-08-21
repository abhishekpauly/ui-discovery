"""V1 integration tests — crawl the local multi-page fixture site over real
HTTP. The site is served from a background thread on an ephemeral port, so the
suite stays self-contained and never touches an external network.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

from ui_discovery.crawler import crawl_site
from ui_discovery.reports import build_markdown, write_reports

SITE = Path(__file__).resolve().parents[1] / "fixtures" / "site"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(SITE)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


@pytest.fixture(scope="module")
def full_crawl(base_url):
    return asyncio.run(
        crawl_site(
            f"{base_url}/index.html",
            max_pages=25,
            max_depth=3,
            output_dir="/tmp/uidisco_test_full",
        )
    )


def test_all_pages_crawled(full_crawl):
    assert full_crawl.stats.pages_crawled == 8
    assert full_crawl.stats.pages_failed == 0


def test_depths_correct(full_crawl):
    by_file = {n.url.rsplit("/", 1)[-1]: n.depth for n in full_crawl.pages}
    assert by_file["index.html"] == 0
    assert by_file["customers.html"] == 1
    assert by_file["orders.html"] == 1
    assert by_file["customer-1.html"] == 2
    assert by_file["order-1002.html"] == 2


def test_external_link_not_crawled(full_crawl):
    urls = {n.url for n in full_crawl.pages}
    assert not any("example.com" in u for u in urls)
    # ...but the external anchor is still recorded as an element on Home.
    home = next(n for n in full_crawl.pages if n.url.endswith("index.html"))
    names = {e.accessible_name for e in home.page.elements}
    assert "External site (should not be crawled)" in names


def test_navigation_edges_present(full_crawl):
    assert full_crawl.stats.links_discovered > 0
    assert any(
        e["from"].endswith("customers.html") and e["to"].endswith("customer-1.html")
        for e in full_crawl.navigation
    )


def test_depth_limit(base_url):
    crawl = asyncio.run(
        crawl_site(
            f"{base_url}/index.html",
            max_pages=25,
            max_depth=1,
            output_dir="/tmp/uidisco_test_d1",
        )
    )
    files = {n.url.rsplit("/", 1)[-1] for n in crawl.pages}
    # Home (0) + its direct links (1); nothing at depth 2.
    assert files == {"index.html", "customers.html", "orders.html", "about.html"}
    assert all((n.depth or 0) <= 1 for n in crawl.pages)


def test_page_budget(base_url):
    crawl = asyncio.run(
        crawl_site(
            f"{base_url}/index.html",
            max_pages=3,
            max_depth=5,
            output_dir="/tmp/uidisco_test_budget",
        )
    )
    # Crawlee's max_requests_per_crawl is an approximate cap under concurrency
    # (a few in-flight requests may finish after the limit is hit), so the
    # budget bounds the crawl without being exact. It must still be far below
    # the full 8-page site.
    assert crawl.stats.pages_crawled < 8
    assert crawl.stats.pages_crawled <= 5


def test_reports_written(full_crawl, tmp_path):
    paths = write_reports(full_crawl, str(tmp_path))
    for p in paths.values():
        assert Path(p).exists()
    md = build_markdown(full_crawl)
    assert "UI Crawl Report" in md
    assert "Page graph" in md
    assert full_crawl.schema_version == "0.1.0"


# --- the page budget is exact (found on a real portal) ----------------------

def test_the_page_budget_is_not_exceeded(serve):
    """`max_pages` used to be approximate. On a slow SPA that retried 29
    requests, a budget of 25 produced 38 captured pages: Crawlee's limit counts
    *completed* requests and is checked before dispatching the next one, so
    anything in flight or being retried does not count yet.

    The handler now claims its budget slot on entry, which is the only place
    that knows how many pages have actually been captured.
    """
    server = serve("fixtures/site")
    for budget in (1, 3, 5):
        crawl = asyncio.run(crawl_site(
            f"{server.base}/index.html", max_pages=budget, max_depth=5,
            output_dir="/tmp/uidisco_budget", probe=False, screenshots=False))
        assert crawl.stats.pages_crawled == budget, (
            f"asked for {budget} pages, captured {crawl.stats.pages_crawled}")


def test_a_truncated_crawl_still_reports_what_it_missed(serve):
    """Holding the budget must not hide that there was more to see."""
    server = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        f"{server.base}/index.html", max_pages=2, max_depth=5,
        output_dir="/tmp/uidisco_budget2", probe=False, screenshots=False))
    captured = {n.url for n in crawl.pages}
    discovered = {e["to"] for e in crawl.navigation}
    assert discovered - captured, "nothing recorded as discovered-but-not-visited"
