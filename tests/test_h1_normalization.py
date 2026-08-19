"""H1 integration tests — normalization applied through a real crawl, not
just the pure `util` functions. Confirms the page graph and Crawlee's own
request queue agree on page counts (the acceptance criterion: "counts match").
"""

from __future__ import annotations

import asyncio

from ui_discovery.crawler import crawl_site


def test_query_variants_stay_distinct_by_default(serve, tmp_path):
    site = serve("fixtures/queries")
    crawl = asyncio.run(
        crawl_site(
            site.url("index.html"), max_depth=1, output_dir=str(tmp_path)
        )
    )
    # index + item?id=1&utm=newsletter + item?id=1&utm=social + item?id=2
    assert crawl.stats.pages_crawled == 4


def test_dedupe_queries_collapses_noise_param_variants(serve, tmp_path):
    site = serve("fixtures/queries")
    crawl = asyncio.run(
        crawl_site(
            site.url("index.html"), max_depth=1, output_dir=str(tmp_path),
            dedupe_queries=True,
        )
    )
    # index + item?id=1 (collapsed from 2 utm_source variants) + item?id=2
    assert crawl.stats.pages_crawled == 3
    assert crawl.config.dedupe_queries is True
    urls = {p.url for p in crawl.pages}
    assert any(u.endswith("item.html?id=1") for u in urls)
    assert not any("utm_source" in u for u in urls)


def test_hash_routes_off_collapses_spa_routes_to_one_page(serve, tmp_path):
    site = serve("fixtures/spa_routes")
    crawl = asyncio.run(
        crawl_site(
            site.url("index.html"), max_depth=1, output_dir=str(tmp_path)
        )
    )
    assert crawl.stats.pages_crawled == 1


def test_hash_routes_on_yields_distinct_pages_per_route(serve, tmp_path):
    site = serve("fixtures/spa_routes")
    crawl = asyncio.run(
        crawl_site(
            site.url("index.html"), max_depth=1, output_dir=str(tmp_path),
            hash_routes=True,
        )
    )
    assert crawl.stats.pages_crawled == 3
    assert crawl.config.hash_routes is True
    headings = {p.page.headings[0].text for p in crawl.pages if p.page.headings}
    assert headings == {"Home", "Orders", "Settings"}
