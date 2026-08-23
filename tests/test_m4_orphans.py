"""M4 — screens that work if you type the URL, and that nothing links to.

`M1` supplies a URL surface; the crawl supplies a navigation graph. The
difference between them is a finding nobody has been able to state from the
artifacts: **a screen that is reachable and unreachable at the same time.**
Dead routes, features shipped without an entry point, admin pages that
outlived their menu item — exactly the things a product owner cannot enumerate
from memory, and exactly what this engine exists to surface.

The mirror case is as useful. A screen with no outbound navigation is either a
leaf or a trap, and a page whose only way onward *leaves the product* is the
sharpest version of that — which is why `H7`'s external edges deliberately do
not count.

Both are **derived, not observed**, and that is why they live on the analysis
rather than on `Page`. The same screen is an orphan or not depending on what
else was captured; recording it on the page model would produce a page whose
meaning changes with its neighbours.

The acceptance that matters most is the negative one: **a fully linked fixture
reports neither.** A finding that fires on healthy input cannot be trusted when
it fires on real input.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.cliconfig import read_url_list
from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.inventory import write_inventory
from ui_discovery.relations import build_relations
from ui_discovery.reports import build_markdown


def _crawl(url, out, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out),
        options=CrawlOptions(max_pages=20, max_depth=3, screenshots=False,
                             probe=False, exclude=["/private/**"], **kw),
    ))


def _short(urls):
    return sorted(u.rsplit("/", 1)[-1] for u in urls)


@pytest.fixture
def mapped(serve, tmp_path):
    """The sitemap fixture, crawled with its sitemap read."""
    server = serve("fixtures/sitemap")
    crawl = _crawl(server.url("index.html"), tmp_path, sitemap="include")
    return server, crawl, build_relations(crawl)


# --- orphans ----------------------------------------------------------------


def test_a_sitemap_only_page_is_an_orphan(mapped):
    """`orphan.html` is in the sitemap and linked from nowhere. Ordinary
    link-following would never find it, so neither would a person browsing."""
    _, _, relations = mapped
    assert "orphan.html" in _short(relations.orphans)


def test_a_linked_page_is_not_an_orphan(mapped):
    """The half that makes the finding worth reading."""
    _, _, relations = mapped
    orphans = _short(relations.orphans)
    assert "linked.html" not in orphans
    assert "deep.html" not in orphans


def test_the_entry_point_is_never_an_orphan(mapped):
    """Nothing links to the front door either, and calling it an orphan would
    make the finding fire on every capture ever taken."""
    _, _, relations = mapped
    assert "index.html" not in _short(relations.orphans)
    assert relations.entry_points


def test_orphans_are_counted_in_the_stats(mapped):
    _, _, relations = mapped
    assert relations.stats["orphan_screens"] == len(relations.orphans)


# --- dead ends --------------------------------------------------------------


def test_a_page_whose_only_link_leaves_the_product_is_a_dead_end(mapped):
    """`deadend.html` links only to a vendor. `H7` records that edge, and it
    deliberately does not count here — a way onward that leaves the product is
    not a way onward through it."""
    _, crawl, relations = mapped
    assert "deadend.html" in _short(relations.dead_ends)
    # And the external edge really is recorded, so this is not just a page
    # with no links at all.
    assert any("vendor.invalid" in e.target for e in crawl.external_links)


def test_a_page_that_links_onward_is_not_a_dead_end(mapped):
    _, _, relations = mapped
    assert "index.html" not in _short(relations.dead_ends)
    assert "linked.html" not in _short(relations.dead_ends)


def test_dead_ends_are_counted_in_the_stats(mapped):
    _, _, relations = mapped
    assert relations.stats["dead_end_screens"] == len(relations.dead_ends)


# --- the negative case ------------------------------------------------------


def test_a_fully_linked_fixture_reports_neither(serve, tmp_path):
    """The acceptance that makes the rest usable: a finding that fires on
    healthy input cannot be trusted when it fires on real input."""
    root = tmp_path / "healthy"
    root.mkdir()
    (root / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Home</title></head><body><main><h1>Home</h1>"
        '<a href="two.html">Two</a></main></body></html>', encoding="utf-8")
    (root / "two.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Two</title></head><body><main><h1>Two</h1>"
        '<a href="index.html">Home</a></main></body></html>', encoding="utf-8")
    server = serve(str(root))

    crawl = _crawl(server.url("index.html"), tmp_path / "out", sitemap="skip")
    relations = build_relations(crawl)
    assert relations.orphans == []
    assert relations.dead_ends == []


# --- stability --------------------------------------------------------------


def test_orphan_status_is_stable_across_two_runs(serve, tmp_path):
    """A finding that moves between runs of an unchanged product is noise.
    Two captures, same fixture, same verdict."""
    server = serve("fixtures/sitemap")
    first = build_relations(
        _crawl(server.url("index.html"), tmp_path / "a", sitemap="include"))
    second = build_relations(
        _crawl(server.url("index.html"), tmp_path / "b", sitemap="include"))
    assert sorted(first.orphans) == sorted(second.orphans)
    assert sorted(first.dead_ends) == sorted(second.dead_ends)


# --- where they surface -----------------------------------------------------


def test_each_screen_carries_its_own_verdict(mapped):
    """Derived and attached to the analysis, not to `Page`: the same screen is
    an orphan or not depending on what else was captured."""
    _, _, relations = mapped
    by_url = {s.url: s for s in relations.screens}
    orphan = next(u for u in relations.orphans if u.endswith("orphan.html"))
    assert by_url[orphan].orphan is True
    assert by_url[relations.entry_points[0]].orphan is False


def test_the_report_names_them(mapped):
    _, crawl, relations = mapped
    markdown = build_markdown(crawl, relations)
    assert "## Reachable, but not from anywhere" in markdown
    assert "orphan screen" in markdown
    assert "dead end" in markdown
    assert any(u in markdown for u in relations.orphans)


def test_the_report_stays_silent_on_a_healthy_site(serve, tmp_path):
    root = tmp_path / "healthy"
    root.mkdir()
    (root / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Home</title></head><body><main><h1>Home</h1>"
        '<a href="two.html">Two</a></main></body></html>', encoding="utf-8")
    (root / "two.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Two</title></head><body><main><h1>Two</h1>"
        '<a href="index.html">Home</a></main></body></html>', encoding="utf-8")
    server = serve(str(root))

    crawl = _crawl(server.url("index.html"), tmp_path / "out", sitemap="skip")
    markdown = build_markdown(crawl, build_relations(crawl))
    assert "## Reachable, but not from anywhere" not in markdown


def test_urls_txt_is_annotated(mapped, tmp_path):
    _, crawl, relations = mapped
    write_inventory(crawl, str(tmp_path / "inv"), relations=relations)
    text = (tmp_path / "inv" / "urls.txt").read_text(encoding="utf-8")
    assert "# orphan" in text
    assert "# dead-end" in text


def test_the_annotated_urls_txt_is_still_consumable_by_h10(mapped, tmp_path):
    """A column would have broken `--from`. The annotation is a trailing
    comment precisely so the file stays something you can filter by hand and
    hand straight back."""
    _, crawl, relations = mapped
    write_inventory(crawl, str(tmp_path / "inv"), relations=relations)

    urls = read_url_list(str(tmp_path / "inv" / "urls.txt"))
    assert urls == [n.url for n in crawl.pages]
    assert all("#" not in u for u in urls)


def test_an_unannotated_screen_has_no_trailing_comment(mapped, tmp_path):
    """Only the findings are marked, so the file stays readable."""
    _, crawl, relations = mapped
    write_inventory(crawl, str(tmp_path / "inv"), relations=relations)
    lines = (tmp_path / "inv" / "urls.txt").read_text(
        encoding="utf-8").strip().splitlines()
    plain = [ln for ln in lines if "#" not in ln]
    assert plain, "a healthy screen should appear without a comment"
