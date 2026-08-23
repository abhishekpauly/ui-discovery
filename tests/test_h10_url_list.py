"""H10 — capture exactly these screens.

Every entry point was a *start URL to crawl from*. There was no way to say
"capture exactly these forty screens", which is what you want when re-checking
a previous run's `urls.txt`, when `M2`'s map has been filtered by hand, or when
a scheduled run should watch a fixed set rather than rediscover the product
every night.

The load-bearing property is the one that is easiest to quietly get wrong:

    **A list is convenience, never an authorization.**

Every entry goes through the same scope gate a discovered link does. If it did
not, `--from` would be a way to talk the engine past its own config by pasting
a URL into a text file — and it would be the most natural way to do it, because
the file looks like input rather than like permission.

The second property is that one bad line does not cost the other thirty-nine.
A forty-URL file with a typo in it should capture thirty-nine screens and tell
you about the fortieth, not abort.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.cliconfig import read_url_list
from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.map import build_map, write_map


@pytest.fixture
def site(serve):
    return serve("fixtures/sitemap")


def _crawl(url, out, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out),
        options=CrawlOptions(max_pages=25, max_depth=3, screenshots=False,
                             probe=False, sitemap="skip", **kw),
    ))


def _names(crawl):
    return sorted(n.url.rsplit("/", 1)[-1] for n in crawl.pages)


# --- reading the file -------------------------------------------------------


def test_blank_lines_and_comments_are_skipped(tmp_path):
    """Crossing a screen out with a `#` is what someone will actually do to a
    map's `urls.txt`. Failing on it would send them back to delete lines."""
    listing = tmp_path / "urls.txt"
    listing.write_text(
        "http://a.test/one\n"
        "\n"
        "# http://a.test/two   <- deliberately not captured\n"
        "   http://a.test/three   \n",
        encoding="utf-8")
    assert read_url_list(str(listing)) == ["http://a.test/one",
                                           "http://a.test/three"]


def test_no_file_is_an_empty_list_not_an_error():
    assert read_url_list(None) == []


def test_an_unreadable_file_exits_cleanly(tmp_path, capsys):
    with pytest.raises(SystemExit):
        read_url_list(str(tmp_path / "missing.txt"))
    assert "Could not read URL list" in capsys.readouterr().err


# --- capturing exactly what was named ---------------------------------------


def test_only_the_named_screens_are_captured(site, tmp_path):
    wanted = [site.url("index.html"), site.url("orphan.html"),
              site.url("reports/quarterly.html")]
    crawl = _crawl(site.url("index.html"), tmp_path, url_list=tuple(wanted))
    assert _names(crawl) == ["index.html", "orphan.html", "quarterly.html"]


def test_no_links_are_followed(site, tmp_path):
    """`index.html` links to `linked.html`, which links to `deep.html`.
    Neither may appear: "capture these" has to mean these."""
    crawl = _crawl(site.url("index.html"), tmp_path,
                   url_list=(site.url("index.html"),))
    assert _names(crawl) == ["index.html"]


def test_the_page_graph_still_records_the_links(site, tmp_path):
    """Only the queue is narrowed. A capture that also forgot the outgoing
    links would be a worse artifact, not a smaller one."""
    crawl = _crawl(site.url("index.html"), tmp_path,
                   url_list=(site.url("index.html"),))
    assert crawl.pages[0].out_links, "the page's own links must survive"


def test_a_duplicate_entry_is_captured_once(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path,
                   url_list=(site.url("index.html"), site.url("index.html")))
    assert _names(crawl) == ["index.html"]


# --- a list is not an authorization -----------------------------------------


def test_an_out_of_scope_entry_is_refused(site, tmp_path):
    """The whole point. If a list could reach past `exclude`, `--from` would
    be a config bypass that looks like input."""
    crawl = _crawl(site.url("index.html"), tmp_path,
                   exclude=["/private/**"],
                   url_list=(site.url("index.html"),
                             site.url("private/secret.html")))
    assert _names(crawl) == ["index.html"]
    assert not any("secret" in n.url for n in crawl.pages)


def test_a_refused_entry_is_reported_in_the_failure_ledger(site, tmp_path):
    """Refused and *silent* would be worse than allowed: the operator would
    believe they had captured a screen they had not."""
    crawl = _crawl(site.url("index.html"), tmp_path,
                   exclude=["/private/**"],
                   url_list=(site.url("index.html"),
                             site.url("private/secret.html")))
    ledger = {f.url.rsplit("/", 1)[-1]: f for f in crawl.failures}
    assert "secret.html" in ledger
    assert ledger["secret.html"].reason == "out-of-scope"
    assert "URL list" in ledger["secret.html"].detail


def test_an_off_site_entry_is_refused_and_reported(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path,
                   url_list=(site.url("index.html"),
                             "https://elsewhere.invalid/x.html"))
    assert _names(crawl) == ["index.html"]
    ledger = {f.url: f.reason for f in crawl.failures}
    assert ledger.get("https://elsewhere.invalid/x.html") == "off-site"


def test_one_bad_line_does_not_cost_the_others(site, tmp_path):
    """A forty-URL file with a typo should capture thirty-nine screens and
    tell you about the fortieth."""
    crawl = _crawl(site.url("index.html"), tmp_path,
                   exclude=["/private/**"],
                   url_list=(site.url("index.html"),
                             site.url("private/secret.html"),
                             "https://elsewhere.invalid/x",
                             site.url("orphan.html")))
    assert _names(crawl) == ["index.html", "orphan.html"]
    assert len(crawl.failures) >= 2


# --- the round trip ---------------------------------------------------------


def test_the_file_map_writes_is_directly_consumable(site, tmp_path):
    """`M2` → `H10` with nothing in between. If the two artifacts did not fit
    together, filtering a map by hand would mean reformatting it first."""
    from ui_discovery.config import Scope

    scope = Scope(start_url=site.url("index.html"))
    url_map = build_map(site.url("index.html"), scope)
    paths = write_map(url_map, str(tmp_path / "map"))

    listing = read_url_list(paths["urls"])
    assert listing, "the map wrote no URLs to feed back"

    crawl = _crawl(site.url("index.html"), tmp_path / "cap",
                   url_list=tuple(listing))
    assert ({n.url for n in crawl.pages}
            == {e.url for e in url_map.entries if e.in_scope})


def test_a_hand_filtered_map_captures_the_kept_lines(site, tmp_path):
    """The realistic workflow: run `map`, delete the screens you do not want,
    hand the file back."""
    from ui_discovery.config import Scope

    scope = Scope(start_url=site.url("index.html"))
    paths = write_map(build_map(site.url("index.html"), scope),
                      str(tmp_path / "map"))

    kept = [u for u in read_url_list(paths["urls"]) if "orphan" in u]
    assert kept, "the fixture must offer something to keep"

    crawl = _crawl(site.url("index.html"), tmp_path / "cap",
                   url_list=tuple(kept))
    assert _names(crawl) == ["orphan.html"]


def test_a_url_list_does_not_ask_the_target_for_a_sitemap(site, tmp_path):
    """An explicit list answers the question the sitemap answers, so reading
    it is pure waste — and would put requests in `G7`'s ledger that this run
    had no reason to make."""
    from ui_discovery.run import RunContext

    run = RunContext.begin(str(tmp_path / "run"), target=site.base)
    asyncio.run(crawl_site(
        site.url("index.html"), output_dir=str(tmp_path / "out"), run=run,
        options=CrawlOptions(max_pages=5, screenshots=False, probe=False,
                             sitemap="include",
                             url_list=(site.url("index.html"),)),
    ))
    paths = [h.first_path for h in run.manifest().egress.hosts]
    assert "/robots.txt" not in paths, paths
