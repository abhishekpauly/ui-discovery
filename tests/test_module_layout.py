"""Where captures are written, and how they are laid out.

A capture is a deliverable — you open it, attach it, hand it over — so it
goes to Downloads, in a folder named after the product, split module by
module. The module folder is the unit you give to the team that owns that
module, so each one has to stand on its own.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ui_discovery.cliconfig import resolve_output_dir
from ui_discovery.config import DOWNLOADS, Scope
from ui_discovery.crawler import crawl_site
from ui_discovery.inventory import assign_modules, write_module_artifacts

MODULES = [("Orders", "http://x.test/orders"),
           ("Customers", "http://x.test/customers")]


# --- where it goes ----------------------------------------------------------

def test_captures_default_to_the_downloads_folder():
    target = resolve_output_dir(Scope(target="Acme Portal"), None, "slug")
    assert target.startswith(DOWNLOADS)
    assert target.endswith("Acme-Portal")


def test_downloads_is_the_users_home_not_the_repo():
    assert DOWNLOADS == str(Path.home() / "Downloads")
    assert "ui-discovery" not in DOWNLOADS


def test_explicit_output_still_wins(tmp_path):
    target = resolve_output_dir(Scope(target="Acme"), str(tmp_path), "slug")
    assert target.startswith(str(tmp_path))


def test_config_dir_beats_the_default(tmp_path):
    scope = Scope.model_validate(
        {"target": "Acme", "outputs": {"dir": str(tmp_path)}})
    assert resolve_output_dir(scope, None, "slug").startswith(str(tmp_path))


# --- how it is laid out -----------------------------------------------------

def _crawl(serve, tmp_path):
    site = serve("fixtures/site")
    return asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3, output_dir=str(tmp_path)))


def test_pages_are_grouped_by_module(serve, tmp_path):
    crawl = _crawl(serve, tmp_path)
    base = crawl.config.start_url.rsplit("/", 1)[0]
    grouped = assign_modules(crawl, [("Orders", f"{base}/orders.html")])
    assert any("orders.html" in n.url for n in grouped.get("Orders", []))
    # Everything else is `general` — most pages on most sites, not a failure.
    assert grouped["general"]


def test_longest_matching_module_wins():
    """A module at /platform/rag/containers must beat one at /platform."""
    from ui_discovery.models import Crawl, CrawlConfig, CrawlStats, Page, PageNode

    def node(url):
        page = Page(schema_version="0", engine_version="0", extracted_at="now",
                    requested_url=url, final_url=url, title="t")
        return PageNode(url=url, page=page)

    crawl = Crawl(
        schema_version="0", engine_version="0", crawl_id="x",
        started_at="a", finished_at="b",
        config=CrawlConfig(start_url="http://x.test/", max_pages=1,
                           max_depth=1, strategy="same-domain"),
        stats=CrawlStats(pages_crawled=1, pages_failed=0, unique_urls=1,
                         links_discovered=0, runtime_seconds=0.0),
        pages=[node("http://x.test/platform/rag/containers")],
    )
    grouped = assign_modules(crawl, [
        ("Platform", "http://x.test/platform"),
        ("RAG", "http://x.test/platform/rag"),
    ])
    assert "RAG" in grouped and "Platform" not in grouped


def test_every_page_lands_in_exactly_one_module(serve, tmp_path):
    """The partition must not lose or duplicate a screen."""
    crawl = _crawl(serve, tmp_path)
    base = crawl.config.start_url.rsplit("/", 1)[0]
    grouped = assign_modules(crawl, [
        ("Orders", f"{base}/orders.html"),
        ("Customers", f"{base}/customers.html"),
    ])
    placed = [n.url for nodes in grouped.values() for n in nodes]
    assert sorted(placed) == sorted(n.url for n in crawl.pages)
    assert len(placed) == len(set(placed))


def test_each_module_folder_stands_on_its_own(serve, tmp_path):
    crawl = _crawl(serve, tmp_path)
    base = crawl.config.start_url.rsplit("/", 1)[0]
    out = tmp_path / "product"
    written = write_module_artifacts(
        crawl, str(out), [("Orders", f"{base}/orders.html")])

    assert "Orders" in written
    folder = Path(written["Orders"])
    for name in ("summary.md", "urls.txt", "elements.csv",
                 "endpoints.md", "inventory.json"):
        assert (folder / name).exists(), f"{name} missing from module folder"

    # Scoped, not a copy of the whole crawl.
    urls = (folder / "urls.txt").read_text(encoding="utf-8").split()
    assert urls and all("orders.html" in u for u in urls)
    inv = json.loads((folder / "inventory.json").read_text(encoding="utf-8"))
    assert inv["screens_count"] == len(urls) < crawl.stats.pages_crawled


def test_module_folders_carry_their_own_screenshots(serve, tmp_path):
    crawl = _crawl(serve, tmp_path)
    base = crawl.config.start_url.rsplit("/", 1)[0]
    written = write_module_artifacts(
        crawl, str(tmp_path / "product"), [("Orders", f"{base}/orders.html")])
    shots = Path(written["Orders"]) / "screenshots"
    assert shots.is_dir()
    assert list(shots.glob("*.png"))


def test_the_canonical_model_is_never_split(serve, tmp_path):
    """crawl.json stays whole at the product level. A partial one would be a
    different, lesser artifact wearing the same name."""
    crawl = _crawl(serve, tmp_path)
    base = crawl.config.start_url.rsplit("/", 1)[0]
    out = tmp_path / "product"
    written = write_module_artifacts(
        crawl, str(out), [("Orders", f"{base}/orders.html")])
    assert not (Path(written["Orders"]) / "crawl.json").exists()


def test_no_modules_means_no_extra_folders(serve, tmp_path):
    """Without modules configured, nothing changes: one flat capture."""
    crawl = _crawl(serve, tmp_path)
    written = write_module_artifacts(crawl, str(tmp_path / "product"), [])
    assert set(written) == {"general"}
