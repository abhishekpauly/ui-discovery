"""Pure-function unit tests — fast, no browser."""

from __future__ import annotations

from ui_discovery.util import (
    bfs_depths,
    normalize_url,
    resolve_links,
    same_site,
    slug_for,
)


def test_normalize_url_drops_fragment_and_trailing_slash():
    assert normalize_url("http://x.com/a/#frag") == "http://x.com/a"
    assert normalize_url("http://x.com/") == "http://x.com/"
    assert normalize_url("http://x.com/a?q=1") == "http://x.com/a?q=1"


def test_same_site():
    assert same_site("http://x.com/a", "http://x.com/b") is True
    assert same_site("http://y.com/a", "http://x.com/b") is False


def test_resolve_links_filters_and_normalizes():
    root = "http://x.com/index.html"
    hrefs = [
        "customers.html",              # relative -> same site
        "https://example.com/",        # external -> excluded
        "mailto:a@b.com",              # non-navigational -> excluded
        "#section",                    # fragment-only -> excluded
        "orders.html#top",             # fragment stripped
        "customers.html",              # duplicate -> deduped
    ]
    out = resolve_links(root, hrefs, root)
    assert out == [
        "http://x.com/customers.html",
        "http://x.com/orders.html",
    ]


def test_bfs_depths():
    edges = {
        "r": ["a", "b"],
        "a": ["c"],
        "b": ["c"],
        "c": [],
    }
    depths = bfs_depths("r", edges)
    assert depths == {"r": 0, "a": 1, "b": 1, "c": 2}


def test_slug_for():
    assert slug_for("https://example.com/docs/api") == "example.com_docs_api"
    assert slug_for("file:///tmp/table.html") == "file_table"
