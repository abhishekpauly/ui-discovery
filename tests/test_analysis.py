"""V2 analysis integration tests — analyze a real crawl of the fixture site."""

from __future__ import annotations

import asyncio
import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

from ui_discovery.analysis import analyze_crawl
from ui_discovery.crawler import crawl_site

SITE = Path(__file__).resolve().parents[1] / "fixtures" / "site"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def analysis():
    port = _free_port()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(SITE)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        crawl = asyncio.run(
            crawl_site(
                f"http://127.0.0.1:{port}/index.html",
                max_depth=3,
                output_dir="/tmp/uidisco_v2",
            )
        )
    finally:
        httpd.shutdown()
    return analyze_crawl(crawl)


def test_all_pages_fingerprinted(analysis):
    assert analysis.stats["pages_analyzed"] == 8
    assert analysis.stats["elements_fingerprinted"] > 0
    # Pages with catalogued main content expose a main region. (Text-only
    # <main> sections — e.g. about/order pages that hold only a heading and a
    # paragraph — legitimately have no catalogued elements, so no main region.)
    home = next(p for p in analysis.pages if p.url.endswith("index.html"))
    assert any(r.type == "main" for r in home.regions)


def test_regions_include_navigation_and_banner(analysis):
    home = next(p for p in analysis.pages if p.url.endswith("index.html"))
    types = {r.type for r in home.regions}
    assert "navigation" in types
    assert "banner" in types
    assert "main" in types


def test_shared_primary_nav_detected(analysis):
    shared = [c for c in analysis.components if c.kind == "shared"]
    # The primary nav links appear on every page -> shared across many pages.
    nav_shared = [c for c in shared if c.landmark == "navigation"]
    assert nav_shared
    assert max(c.page_count for c in nav_shared) >= 5


def test_repeated_view_links_detected(analysis):
    repeated = [c for c in analysis.components if c.kind == "repeated"]
    # customers.html and orders.html each have two "View" links in a table row.
    assert any(c.role == "link" and c.instance_count >= 2 for c in repeated)


def test_navigation_menu_extracted(analysis):
    assert analysis.navigations
    # Menus are grouped by exact (label, items). The full 4-item primary nav
    # appears on the top-level pages (index/customers/orders/about); deeper
    # pages show trimmed variants of the same nav.
    primary = max(analysis.navigations, key=lambda n: n.page_count)
    assert "Home" in primary.items
    assert primary.page_count >= 4


def test_fingerprint_strategy_distribution_recorded(analysis):
    # Our fixtures use human-authored ids/labels + structure; strategies recorded.
    assert any(k.startswith("fp_strategy_") for k in analysis.stats)


def test_source_crawl_linked(analysis):
    assert analysis.source_crawl_id
    assert analysis.schema_version == "0.1.0"
