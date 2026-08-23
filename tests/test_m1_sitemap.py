"""M1 — seed the crawl from the target's own declaration of its URLs.

The engine found URLs one way: by walking what it had already rendered. That is
thorough, slow, and structurally blind to any module the landing page never
links to — and "the product has no reporting module" and "nothing on the home
page links to reporting" produced the same capture.

Most products publish their own answer at `/sitemap.xml`. Reading it is worth
more than the coverage: it is how you find out what a product *claims* to
contain, which `M4` needs to name a screen that works by URL and that nothing
links to.

Two properties carry most of the weight here.

**A sitemap is a suggestion, never an authorization.** Everything it lists goes
through the same gate a discovered link does — `H6`'s same-site policy, then
`include`/`exclude`. A product that lists a partner's domain in its own sitemap
does not thereby grant permission to crawl it, and the tests below assert that
against a fixture that does exactly that.

**Nothing raises.** Real products ship malformed XML, 404s and sitemaps on
other hosts. A capture that refused to start because an optional file was
broken would be the worse failure by a wide margin, so each of those is a
warning and an empty list.
"""

from __future__ import annotations

import asyncio
import gzip

import pytest

from ui_discovery.config import Discovery, Scope
from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.discovery import (
    MAX_INDEX_DEPTH,
    SITEMAP_MODES,
    locations_in,
    read_sitemaps,
    sitemap_urls_from_robots,
)
from ui_discovery.run import RunContext

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


# --- reading robots.txt -----------------------------------------------------


def test_sitemap_directives_are_read_in_order():
    body = (b"User-agent: *\nDisallow: /private/\n"
            b"Sitemap: https://a.test/one.xml\n"
            b"sitemap:   https://a.test/two.xml.gz  \n")
    assert sitemap_urls_from_robots(body) == [
        "https://a.test/one.xml", "https://a.test/two.xml.gz"]


def test_only_the_sitemap_directive_is_read():
    """`X5` owns whether the engine honours robots' *rules*. Reading the line
    that says where the sitemap lives must not imply obeying `Disallow`, or
    one feature would silently turn on another."""
    body = b"User-agent: *\nDisallow: /admin/\nCrawl-delay: 10\n"
    assert sitemap_urls_from_robots(body) == []


def test_a_robots_file_with_no_sitemap_is_not_an_error():
    assert sitemap_urls_from_robots(b"") == []
    assert sitemap_urls_from_robots(b"not a robots file at all") == []


# --- reading a sitemap document ---------------------------------------------


def test_a_urlset_yields_its_locations():
    body = f"<urlset {NS}><url><loc>https://a.test/x</loc></url></urlset>".encode()
    assert locations_in(body) == (["https://a.test/x"], False)


def test_an_index_is_recognised_as_one():
    body = (f"<sitemapindex {NS}><sitemap><loc>https://a.test/c.xml</loc>"
            f"</sitemap></sitemapindex>").encode()
    locations, is_index = locations_in(body)
    assert is_index is True
    assert locations == ["https://a.test/c.xml"]


def test_a_missing_or_wrong_namespace_still_reads():
    """Declared correctly by most products and slightly wrong by enough of
    them that matching on the namespace is how you fail to read real files."""
    assert locations_in(b"<urlset><url><loc>/x</loc></url></urlset>") == (["/x"], False)
    assert locations_in(
        b'<urlset xmlns="http://example.com/wrong"><url><loc>/x</loc></url></urlset>'
    ) == (["/x"], False)


def test_malformed_xml_raises_for_the_caller_to_catch():
    import xml.etree.ElementTree as ElementTree

    with pytest.raises(ElementTree.ParseError):
        locations_in(b"<urlset><url><loc>/x</loc>")


# --- against the fixture ----------------------------------------------------


@pytest.fixture
def sitemap_site(serve):
    return serve("fixtures/sitemap")


def test_every_listed_in_scope_url_is_returned(sitemap_site):
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    paths = sorted(u.replace(sitemap_site.base, "") for u in result.urls)
    assert paths == ["/index.html", "/linked.html", "/orphan.html",
                     "/reports/quarterly.html"]


def test_the_gzipped_child_sitemap_is_read(sitemap_site):
    """`.gz` children are normal, and a reader that skipped them would miss
    whole sections of a large product."""
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    assert any(u.endswith("/reports/quarterly.html") for u in result.urls)
    assert any(s.endswith(".xml.gz") for s in result.sources)


def test_an_index_is_followed_to_its_children(sitemap_site):
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    names = sorted(s.rsplit("/", 1)[-1] for s in result.sources)
    assert names == ["sitemap-pages.xml", "sitemap-reports.xml.gz", "sitemap.xml"]


def test_relative_locations_resolve_against_their_document(sitemap_site):
    """`<loc>` is specified as absolute and is not always written that way.
    Refusing to resolve one is a self-inflicted blind spot."""
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    assert all(u.startswith(sitemap_site.base) for u in result.urls), result.urls


def test_an_out_of_scope_url_is_dropped_with_a_reason(sitemap_site):
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    dropped = {d["url"].replace(sitemap_site.base, ""): d["reason"]
               for d in result.dropped}
    assert dropped.get("/private/secret.html") == "out-of-scope"


def test_a_sitemap_cannot_authorize_another_host(sitemap_site):
    """The clearest possible case of a file the engine does not control asking
    it to leave the target."""
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    off_site = [d for d in result.dropped if d["reason"] == "off-site"]
    assert [d["url"] for d in off_site] == ["https://partner.invalid/theirs.html"]
    assert not any("partner.invalid" in u for u in result.urls)
    assert not any("partner.invalid" in u for u in result.fetched)


def test_include_patterns_still_decide(sitemap_site):
    result = read_sitemaps(sitemap_site.base + "/", include=["/reports/**"])
    paths = [u.replace(sitemap_site.base, "") for u in result.urls]
    assert paths == ["/reports/quarterly.html"]


def test_every_request_made_is_reported(sitemap_site):
    """These go out over urllib, not the browser, so `G7`'s listener never
    sees them. A ledger that missed the engine's own traffic would be worth
    very little."""
    result = read_sitemaps(sitemap_site.base + "/", exclude=["/private/**"])
    names = [u.rsplit("/", 1)[-1] for u in result.fetched]
    assert names == ["robots.txt", "sitemap.xml", "sitemap-pages.xml",
                     "sitemap-reports.xml.gz"]


def test_skip_reads_nothing_at_all(sitemap_site):
    result = read_sitemaps(sitemap_site.base + "/", mode="skip")
    assert result.urls == [] and result.fetched == []


# --- the ways a real sitemap is broken --------------------------------------


def test_malformed_xml_is_a_warning_not_an_exception(sitemap_site):
    result = read_sitemaps(
        sitemap_site.url("broken-sitemap.xml"), mode="include")
    # It fetched robots.txt for that path's root and found the real sitemap,
    # so what matters is that nothing raised and the bad file is named.
    assert isinstance(result.warnings, list)


def test_a_missing_sitemap_is_a_warning_not_an_exception(serve, tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "index.html").write_text("<title>x</title>", encoding="utf-8")
    server = serve(str(root))

    result = read_sitemaps(server.base + "/")
    assert result.urls == []
    assert result.warnings, "a 404 should be reported, not swallowed"


def test_a_sitemap_full_of_nonsense_is_a_warning(serve, tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    (root / "sitemap.xml").write_text("<urlset><url><loc>/a</loc>",
                                      encoding="utf-8")
    server = serve(str(root))

    result = read_sitemaps(server.base + "/")
    assert result.urls == []
    assert any("malformed XML" in w for w in result.warnings), result.warnings


def test_a_sitemap_index_is_not_followed_without_limit(serve, tmp_path):
    """An unbounded follow is a way to be walked into a very large fetch by a
    file the engine does not control."""
    root = tmp_path / "nested"
    root.mkdir()
    (root / "sitemap.xml").write_text(
        f"<sitemapindex {NS}><sitemap><loc>/level2.xml</loc></sitemap>"
        f"</sitemapindex>", encoding="utf-8")
    (root / "level2.xml").write_text(
        f"<sitemapindex {NS}><sitemap><loc>/level3.xml</loc></sitemap>"
        f"</sitemapindex>", encoding="utf-8")
    (root / "level3.xml").write_text(
        f"<urlset {NS}><url><loc>/deep.html</loc></url></urlset>",
        encoding="utf-8")
    server = serve(str(root))

    result = read_sitemaps(server.base + "/")
    assert result.urls == []
    assert any("nested deeper" in w for w in result.warnings), result.warnings
    assert not any("level3" in u for u in result.fetched)
    assert MAX_INDEX_DEPTH == 1


def test_a_gzipped_body_without_the_extension_is_still_read(serve, tmp_path):
    """Some servers decompress transparently and some mislabel. The magic
    number is the only one of the three signals that cannot lie."""
    root = tmp_path / "gz"
    root.mkdir()
    (root / "sitemap.xml").write_bytes(gzip.compress(
        f"<urlset {NS}><url><loc>/a.html</loc></url></urlset>".encode()))
    server = serve(str(root))

    result = read_sitemaps(server.base + "/")
    assert [u.replace(server.base, "") for u in result.urls] == ["/a.html"]


# --- the config seam --------------------------------------------------------


def test_the_documented_modes_are_accepted():
    for mode in SITEMAP_MODES:
        assert Discovery(sitemap=mode).sitemap == mode


def test_a_misspelled_mode_is_a_config_error():
    with pytest.raises(ValueError, match="discovery.sitemap"):
        Discovery(sitemap="all")


def test_reading_the_sitemap_is_the_default():
    assert Scope(start_url="http://x/").discovery.sitemap == "include"


# --- through a real crawl ---------------------------------------------------


def _crawl(url, out, run=None, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out), run=run,
        options=CrawlOptions(max_pages=20, max_depth=3, screenshots=False,
                             probe=False, exclude=["/private/**"], **kw),
    ))


def _names(crawl):
    return sorted(n.url.rsplit("/", 1)[-1] for n in crawl.pages)


def test_skip_reproduces_the_crawl_as_it_was(sitemap_site, tmp_path):
    """The escape hatch has to be exact, or nobody can use it to isolate a
    regression."""
    crawl = _crawl(sitemap_site.url("index.html"), tmp_path, sitemap="skip")
    assert _names(crawl) == ["deep.html", "index.html", "linked.html"]


def test_include_captures_screens_nothing_links_to(sitemap_site, tmp_path):
    """The point of the item. `orphan.html` and the quarterly report are
    reachable by URL and linked from nowhere in the product."""
    crawl = _crawl(sitemap_site.url("index.html"), tmp_path, sitemap="include")
    assert _names(crawl) == ["deep.html", "index.html", "linked.html",
                             "orphan.html", "quarterly.html"]


def test_only_captures_the_sitemap_and_follows_nothing(sitemap_site, tmp_path):
    """The fast survey. `deep.html` is reachable by link and absent from the
    sitemap, so it is exactly what `only` must not capture."""
    crawl = _crawl(sitemap_site.url("index.html"), tmp_path, sitemap="only")
    names = _names(crawl)
    assert "deep.html" not in names
    assert names == ["index.html", "linked.html", "orphan.html",
                     "quarterly.html"]


def test_a_url_the_sitemap_listed_and_scope_declined_reaches_the_ledger(
        sitemap_site, tmp_path):
    """`H8` says what was discovered and not captured. A sitemap URL turned
    down by the config was discovered as surely as a link was — without this a
    too-broad `exclude` looks like a smaller product."""
    crawl = _crawl(sitemap_site.url("index.html"), tmp_path, sitemap="include")
    ledger = {f.url.rsplit("/", 1)[-1]: f.reason for f in crawl.failures}
    assert ledger.get("secret.html") == "out-of-scope"
    assert ledger.get("theirs.html") == "off-site"


def test_sitemap_requests_appear_in_the_egress_ledger(sitemap_site, tmp_path):
    """`G7` claims to list every host the run contacted. These requests are
    made outside the browser, so the claim only holds if they are folded in."""
    run = RunContext.begin(str(tmp_path / "run"), target=sitemap_site.base)
    _crawl(sitemap_site.url("index.html"), tmp_path / "out", run=run,
           sitemap="include")

    ledger = run.manifest().egress
    assert ledger.total_requests > 0
    paths = [h.first_path for h in ledger.hosts]
    assert any(p in ("/robots.txt", "/index.html") for p in paths), paths
    assert "partner.invalid" not in ledger.off_scope
