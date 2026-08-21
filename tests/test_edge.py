"""Rainy-day / edge-case functional tests across V0–V3.

These deliberately feed the engine adversarial input — empty, malformed,
hidden, unicode, deeply-nested pages; a crawl site full of broken/duplicate/
non-HTML links; and pages whose controls navigate away or carry secrets — and
assert it degrades gracefully rather than crashing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ui_discovery import SCHEMA_VERSION, __version__
from ui_discovery.analysis import analyze_crawl
from ui_discovery.crawler import crawl_site
from ui_discovery.extraction import extract_page
from ui_discovery.interactions import probe_page
from ui_discovery.models import (
    Crawl,
    CrawlConfig,
    CrawlStats,
    Page,
    PageNode,
)

EDGE = Path(__file__).resolve().parents[1] / "fixtures" / "edge"


def _file(name: str) -> str:
    return (EDGE / name).resolve().as_uri()


# =========================================================================
# V0 — extractor robustness
# =========================================================================

def test_v0_empty_page():
    page = extract_page(_file("empty.html"))
    assert page.title == ""
    assert page.counts.get("total_elements", 0) == 0
    assert page.readiness.get("body_present") is True


def test_v0_malformed_does_not_crash_and_finds_elements():
    page = extract_page(_file("malformed.html"))
    cats = {e.category for e in page.elements}
    assert "button" in cats and "link" in cats
    assert page.title.startswith("Malformed")


def test_v0_duplicate_ids_get_distinct_dom_paths():
    # Regression: the #id shortcut must not collapse two dup-id buttons.
    page = extract_page(_file("malformed.html"))
    dup = [e for e in page.elements if e.attributes.get("id") == "dup"]
    assert len(dup) == 2
    assert dup[0].dom_path != dup[1].dom_path


def test_v0_hidden_variants_marked_not_visible():
    page = extract_page(_file("hidden_variants.html"))
    by_text = {(e.text or "").strip(): e for e in page.elements if e.category == "button"}
    for hidden in ("display none", "visibility hidden", "opacity zero",
                   "zero size", "hidden attribute"):
        assert by_text[hidden].visible is False, hidden
    assert by_text["Genuinely visible"].visible is True


def test_v0_hidden_input_excluded():
    page = extract_page(_file("hidden_variants.html"))
    assert not any(
        e.category == "input" and e.attributes.get("type") == "hidden"
        for e in page.elements
    )


def test_v0_unicode_roundtrips():
    page = extract_page(_file("unicode.html"))
    dumped = page.model_dump_json()  # must serialize without error
    assert "🚀" in page.title
    names = {e.accessible_name for e in page.elements}
    assert "删除客户 🗑️" in names
    assert "🚀" in dumped


def test_v0_deeply_nested_no_crash():
    page = extract_page(_file("deep.html"))
    buried = [e for e in page.elements if e.text == "Deeply buried button"]
    assert buried and buried[0].dom_path  # path captured (depth-capped)


# =========================================================================
# V1 — crawler resilience
# =========================================================================

def test_v1_broken_links_and_non_html(serve):
    srv = serve("fixtures/edge/site")
    crawl = asyncio.run(crawl_site(srv.url("index.html"), max_depth=3,
                                   output_dir="/tmp/uidisco_edge_site"))
    urls = {n.url for n in crawl.pages}
    # The crawl completes despite a 404 link and a non-HTML resource.
    assert any(u.endswith("good.html") for u in urls)
    # External domain is never crawled.
    assert not any("example.com" in u for u in urls)
    # Whatever happened to the broken link, it did not abort the crawl.
    assert crawl.stats.pages_crawled >= 2


def test_v1_max_depth_zero_is_start_page_only(serve):
    srv = serve("fixtures/edge/site")
    crawl = asyncio.run(crawl_site(srv.url("index.html"), max_depth=0,
                                   output_dir="/tmp/uidisco_edge_d0"))
    assert crawl.stats.pages_crawled == 1


def test_v1_start_url_404_does_not_crash(serve):
    srv = serve("fixtures/edge/site")
    crawl = asyncio.run(crawl_site(srv.url("does-not-exist.html"), max_depth=1,
                                   output_dir="/tmp/uidisco_edge_404"))
    # No pages successfully modelled, but a valid Crawl object is returned.
    assert isinstance(crawl, Crawl)
    assert crawl.stats.pages_crawled == 0


# =========================================================================
# V2 — analysis on degenerate input
# =========================================================================

def _empty_crawl(pages) -> Crawl:
    return Crawl(
        schema_version=SCHEMA_VERSION, engine_version=__version__,
        crawl_id="edge0", started_at="t", finished_at="t",
        config=CrawlConfig(start_url="http://x", max_pages=1, max_depth=1,
                           strategy="same-domain"),
        stats=CrawlStats(pages_crawled=len(pages), pages_failed=0,
                         unique_urls=len(pages), links_discovered=0,
                         runtime_seconds=0.0),
        navigation=[], pages=pages,
    )


def test_v2_empty_crawl():
    analysis = analyze_crawl(_empty_crawl([]))
    assert analysis.stats["pages_analyzed"] == 0
    assert analysis.components == []
    assert analysis.navigations == []


def test_v2_page_with_no_elements():
    blank = Page(
        schema_version=SCHEMA_VERSION, engine_version=__version__,
        extracted_at="t", requested_url="http://x/p", final_url="http://x/p",
        title="Blank", counts={}, headings=[], elements=[],
    )
    analysis = analyze_crawl(_empty_crawl([PageNode(url="http://x/p", page=blank)]))
    assert analysis.stats["pages_analyzed"] == 1
    assert analysis.stats["elements_fingerprinted"] == 0
    assert analysis.pages[0].regions == []


# =========================================================================
# V3 — probe safety & recovery on hostile pages
# =========================================================================

def test_v3_no_interactive_elements(serve):
    srv = serve("fixtures/edge")
    probe = probe_page(srv.url("nointeractive.html"))
    assert probe.stats["executed"] == 0
    assert probe.stats["elements_seen"] == 0


def test_v3_navigation_is_recovered(serve):
    srv = serve("fixtures/edge")
    probe = probe_page(srv.url("navigating.html"))
    # The page never gets left: probe ends back on the original URL.
    assert probe.final_url.endswith("navigating.html")
    goto = next(i for i in probe.interactions if i.target == "Open page 2")
    assert goto.executed and goto.route_changed and goto.reverted


def test_v3_secret_token_is_redacted(serve):
    srv = serve("fixtures/edge")
    probe = probe_page(srv.url("navigating.html"))
    joined = " ".join(n.url for n in probe.network)
    assert "SECRET123" not in joined
    assert any("REDACTED" in n.url for n in probe.network)


def test_v3_hash_route_change_detected(serve):
    srv = serve("fixtures/edge")
    probe = probe_page(srv.url("navigating.html"))
    hash_tab = next(i for i in probe.interactions if i.target == "Hash route")
    assert hash_tab.executed and hash_tab.route_changed
