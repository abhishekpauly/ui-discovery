"""H2 — the safe interaction/network probe runs on every crawled page.

The V3 probe already proved the safety rules on a single page; these tests
prove they still hold when the probe runs inside the crawl, on pages the
crawler has open, without knocking the crawl off course.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.crawler import crawl_site


@pytest.fixture(scope="module")
def probed(tmp_path_factory):
    from tests.conftest import Server
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    server = Server(root / "fixtures" / "probe_site")
    try:
        return asyncio.run(
            crawl_site(
                f"{server.base}/index.html",
                max_depth=2,
                output_dir=str(tmp_path_factory.mktemp("h2")),
                probe=True,
            )
        )
    finally:
        server.stop()


def test_probe_absent_by_default(serve, tmp_path):
    site = serve("fixtures/probe_site")
    crawl = asyncio.run(
        crawl_site(site.url("index.html"), max_depth=1, output_dir=str(tmp_path))
    )
    assert crawl.config.probe is False
    assert all(node.probe is None for node in crawl.pages)


def test_every_crawled_page_is_probed(probed):
    assert probed.config.probe is True
    assert len(probed.pages) >= 2
    for node in probed.pages:
        assert node.probe is not None, f"no probe for {node.url}"
        assert node.probe.url == node.url


def test_safe_controls_are_executed_on_each_page(probed):
    # Home has a disclosure + native details; settings has two tabs.
    for node in probed.pages:
        assert node.probe.stats["executed"] > 0, f"nothing executed on {node.url}"


def test_destructive_control_is_refused(probed):
    settings = [n for n in probed.pages if n.url.endswith("settings.html")]
    assert settings, "settings page was not crawled"
    interactions = settings[0].probe.interactions

    delete = [i for i in interactions if "delete account" in (i.target or "").lower()]
    assert delete, "the destructive control was not discovered"
    assert all(i.safety_label == "BLOCK" for i in delete)
    assert all(not i.executed for i in delete)


def test_network_is_observed_per_page(probed):
    # Both fixture pages fetch JSON; the sink is attached pre-navigation, so
    # page-load traffic is captured, not just interaction-triggered requests.
    by_url = {n.url: n.probe for n in probed.pages}
    home = next(p for u, p in by_url.items() if u.endswith("index.html"))
    assert home.stats["network_requests"] > 0
    assert any("data.json" in n.url for n in home.network)


def test_probe_folded_into_crawl_report(probed, tmp_path):
    from ui_discovery.reports import build_html, build_markdown

    md = build_markdown(probed)
    assert "## Interaction & network probe (all pages)" in md
    assert "Refused as destructive (BLOCK):" in md
    assert "- Probe:" in md  # per-page line

    page_html = build_html(probed)
    assert "Interaction &amp; network probe" in page_html
    assert "Refused (destructive)" in page_html


def test_report_omits_probe_sections_when_not_probed(serve, tmp_path):
    from ui_discovery.reports import build_html, build_markdown

    site = serve("fixtures/probe_site")
    crawl = asyncio.run(
        crawl_site(site.url("index.html"), max_depth=1, output_dir=str(tmp_path))
    )
    assert "Interaction & network probe" not in build_markdown(crawl)
    assert "Interaction &amp; network probe" not in build_html(crawl)


def test_crawl_still_finds_all_pages_while_probing(serve, tmp_path):
    # The probe clicks things; route-recovery must keep link discovery intact.
    site = serve("fixtures/probe_site")
    plain = asyncio.run(
        crawl_site(site.url("index.html"), max_depth=2, output_dir=str(tmp_path))
    )
    probed_crawl = asyncio.run(
        crawl_site(
            site.url("index.html"), max_depth=2,
            output_dir=str(tmp_path), probe=True,
        )
    )
    assert {n.url for n in probed_crawl.pages} == {n.url for n in plain.pages}
