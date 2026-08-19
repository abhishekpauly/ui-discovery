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


# --- H1: query dedupe --------------------------------------------------------

def test_dedupe_queries_off_by_default_keeps_query_as_is():
    assert normalize_url("http://x.com/a?utm_source=x&id=1") == \
        "http://x.com/a?utm_source=x&id=1"


def test_dedupe_queries_strips_noise_params_and_sorts_the_rest():
    a = normalize_url("http://x.com/a?id=1&utm_source=newsletter", dedupe_queries=True)
    b = normalize_url("http://x.com/a?utm_source=social&id=1", dedupe_queries=True)
    assert a == b == "http://x.com/a?id=1"


def test_dedupe_queries_keeps_distinct_real_params_distinct():
    a = normalize_url("http://x.com/a?id=1", dedupe_queries=True)
    b = normalize_url("http://x.com/a?id=2", dedupe_queries=True)
    assert a != b


def test_dedupe_queries_extra_drop_params():
    a = normalize_url(
        "http://x.com/a?id=1&custom_noise=x", dedupe_queries=True,
        drop_params=frozenset({"custom_noise"}),
    )
    assert a == "http://x.com/a?id=1"


# --- H1: hash routes ---------------------------------------------------------

def test_hash_routes_off_by_default_drops_fragment():
    assert normalize_url("http://x.com/app#/orders") == "http://x.com/app"


def test_hash_routes_on_keeps_route_style_fragment():
    assert normalize_url("http://x.com/app#/orders", hash_routes=True) == \
        "http://x.com/app#/orders"


def test_hash_routes_on_still_drops_bare_anchor_fragment():
    # `#section` is an in-page jump, not a route, hash_routes or not.
    assert normalize_url("http://x.com/app#section", hash_routes=True) == \
        "http://x.com/app"


def test_resolve_links_hash_routes():
    root = "http://x.com/app"
    hrefs = ["#/orders", "#/settings", "#section", "#"]
    assert resolve_links(root, hrefs, root, hash_routes=True) == [
        "http://x.com/app#/orders",
        "http://x.com/app#/settings",
    ]
    # Off by default: all fragment hrefs excluded, same as before H1.
    assert resolve_links(root, hrefs, root) == []


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
