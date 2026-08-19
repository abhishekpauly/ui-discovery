"""Route coverage — reaching screens that link-following alone cannot.

Two failure modes, from a real portal:

  * routes behind a **collapsed nav menu** — their anchors are not in the DOM
    until something is clicked, so the crawler never sees them;
  * routes on an **island** — a contextual sidebar means nothing reachable
    from the start URL links to them at all. No amount of crawling finds
    those; you have to seed them.

Plus the quieter failure: a crawl that ran out of budget and reported
success anyway.
"""

from __future__ import annotations

import asyncio

from ui_discovery.crawler import crawl_site
from ui_discovery.inventory import build_inventory


def _urls(crawl) -> set[str]:
    return {n.url.rsplit("/", 1)[-1] for n in crawl.pages}


def test_collapsed_nav_is_expanded_to_find_hidden_routes(serve, tmp_path):
    site = serve("fixtures/hidden_nav")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=2, output_dir=str(tmp_path)))
    found = _urls(crawl)
    assert "visible.html" in found
    assert "hidden-a.html" in found, f"nav not expanded; got {found}"
    assert "hidden-b.html" in found


def test_reveal_can_be_turned_off(serve, tmp_path):
    site = serve("fixtures/hidden_nav")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=2, output_dir=str(tmp_path),
        reveal_nav=False))
    found = _urls(crawl)
    assert "visible.html" in found
    assert "hidden-a.html" not in found


def test_revealing_never_clicks_a_destructive_control(serve, tmp_path):
    """The reveal pass runs inside navigation landmarks, which is exactly
    where a "Delete workspace" button also lives. It must still be refused."""
    site = serve("fixtures/hidden_nav")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=0, max_pages=1,
        output_dir=str(tmp_path)))
    page = crawl.pages[0].page
    names = {e.accessible_name for e in page.elements}
    assert "Delete workspace" in names  # observed...

    # ...and refused. The reveal pass gates on the same classifier the probe
    # does, so a destructive label in a nav landmark is never clicked.
    from ui_discovery.safety import classify_label, decide, should_execute

    assert classify_label("Delete workspace") == "BLOCK"
    danger = next(e for e in page.elements
                  if e.accessible_name == "Delete workspace")
    assert not should_execute(decide(danger.model_dump()))


def test_seeds_reach_an_island_nothing_links_to(serve, tmp_path):
    site = serve("fixtures/hidden_nav")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=2, output_dir=str(tmp_path),
        seeds=(site.url("island.html"),)))
    assert "island.html" in _urls(crawl)


def test_without_a_seed_the_island_stays_unreachable(serve, tmp_path):
    site = serve("fixtures/hidden_nav")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3, output_dir=str(tmp_path)))
    assert "island.html" not in _urls(crawl)


def test_config_modules_become_seeds():
    from ui_discovery.cliconfig import crawl_options
    from ui_discovery.config import Scope

    class _Args:
        pass

    scope = Scope.model_validate({"modules": [
        {"name": "kh", "start_url": "https://x.test/knowledge-store"},
        {"name": "ds", "start_url": "https://x.test/datasets"},
    ]})
    assert crawl_options(scope, _Args()).seeds == (
        "https://x.test/knowledge-store", "https://x.test/datasets")


def test_a_truncated_crawl_says_so(serve, tmp_path):
    """Discovered-but-not-visited is the difference between "the site has 3
    screens" and "we looked at 3 of them"."""
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3, max_pages=2,
        output_dir=str(tmp_path)))
    inv = build_inventory(crawl)
    assert inv["budget_exhausted"] is True
    assert inv["discovered_not_captured"]


def test_a_complete_crawl_reports_no_truncation(serve, tmp_path):
    site = serve("fixtures/hidden_nav")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3, max_pages=50,
        output_dir=str(tmp_path), seeds=(site.url("island.html"),)))
    inv = build_inventory(crawl)
    assert inv["budget_exhausted"] is False
    assert inv["discovered_not_captured"] == []
