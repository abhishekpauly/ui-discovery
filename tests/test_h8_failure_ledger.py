"""H8 — what the crawl did not capture.

A capture reported what it found and said nothing about what it missed.
`stats.discovered_not_captured` was a single integer and the URLs behind it
were gone, so the question a reader actually has — *is this product 40 screens,
or 60 screens with 20 failures?* — could not be answered from the artifacts at
all.

That is not a cosmetic gap. A report that lists only successes overstates its
own coverage, and the two readings ("this product has no admin module" versus
"we could not reach the admin module") look identical.

The ledger is deliberately **the same fact** as the integer rather than a
second tally beside it: it is built by annotating the very set
`discovered_not_captured` is computed from. A parallel count would eventually
disagree with it, and the disagreement would be silent.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.inventory import build_inventory, write_inventory


def _crawl(url, out, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out),
        options=CrawlOptions(screenshots=False, probe=False, **kw),
    ))


@pytest.fixture
def site(serve):
    return serve("fixtures/site")


def test_a_budget_capped_crawl_lists_every_url_it_dropped(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=2, max_depth=3)

    assert crawl.failures, "a capped crawl must say what it did not reach"
    assert len(crawl.pages) == 2
    reasons = {f.reason for f in crawl.failures}
    assert reasons <= {"budget", "not-reached"}, reasons


def test_the_ledger_matches_discovered_not_captured(site, tmp_path):
    """Asserted rather than left to the eye — the existing list and the new
    ledger are one fact, and this is the test that keeps them one."""
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=3, max_depth=3)
    inventory = build_inventory(crawl)
    assert len(crawl.failures) == len(inventory["discovered_not_captured"])
    assert ({f.url for f in crawl.failures}
            == set(inventory["discovered_not_captured"]))


def test_an_uncapped_crawl_of_a_healthy_site_has_an_empty_ledger(site, tmp_path):
    """The quiet case must stay quiet, or the section becomes noise that
    readers learn to skip."""
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=50, max_depth=5)
    assert crawl.failures == []
    assert build_inventory(crawl)["discovered_not_captured"] == []


def test_every_failure_carries_a_reason_and_a_depth(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=2, max_depth=3)
    for failure in crawl.failures:
        assert failure.url
        assert failure.reason
        assert failure.detail, f"{failure.url} has no explanation"
        # Depth is what tells a reader whether the missing screens were near
        # the entry point or deep in the product.
        assert failure.depth is None or failure.depth >= 0


def test_a_url_the_scope_rules_declined_is_recorded_as_such(site, tmp_path):
    """A deliberate exclusion and an unreachable page are different findings
    and must not read the same."""
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=50, max_depth=5,
                   exclude=["/orders*"])

    out_of_scope = [f for f in crawl.failures if f.reason == "out-of-scope"]
    assert out_of_scope, [f.model_dump() for f in crawl.failures]
    assert all("orders" in f.url for f in out_of_scope)
    assert all("scope rules" in f.detail for f in out_of_scope)


def test_a_broken_link_is_recorded_with_its_status(serve, tmp_path):
    """A page that 404s never reaches the default handler, so before `H8` the
    only trace it left was an increment of `stats.pages_failed`."""
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Home</title></head><body><main><h1>Home</h1>"
        '<a href="gone.html">A link to nowhere</a>'
        "</main></body></html>", encoding="utf-8")
    server = serve(str(root))

    crawl = _crawl(server.url("index.html"), tmp_path / "out",
                   max_pages=10, max_depth=2)

    broken = [f for f in crawl.failures if "gone.html" in f.url]
    assert broken, [f.model_dump() for f in crawl.failures]
    assert broken[0].reason == "error"
    assert broken[0].detail


def test_the_summary_names_what_was_missed_and_why(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=2, max_depth=3)
    write_inventory(crawl, str(tmp_path))

    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "## Not captured" in summary
    assert crawl.failures[0].url in summary
    # The reason is on the row, not just the URL. The old section attributed
    # every miss to the page budget, which made a broken link unreadable.
    assert crawl.failures[0].reason in summary


def test_the_summary_stays_silent_when_nothing_was_missed(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path, max_pages=50, max_depth=5)
    write_inventory(crawl, str(tmp_path))
    assert "## Not captured" not in (tmp_path / "summary.md").read_text(
        encoding="utf-8")


def test_the_incomplete_banner_does_not_blame_the_budget_for_a_broken_link(
        serve, tmp_path):
    """Only one of "budget exhausted" and "the link is broken" is fixed by
    raising `--max-pages`, and telling someone to do that for a 404 sends them
    the wrong way."""
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Home</title></head><body><main><h1>Home</h1>"
        '<a href="gone.html">Nowhere</a></main></body></html>',
        encoding="utf-8")
    server = serve(str(root))

    crawl = _crawl(server.url("index.html"), tmp_path / "out",
                   max_pages=10, max_depth=2)
    write_inventory(crawl, str(tmp_path / "out"))
    summary = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")

    assert "error" in summary
    assert "Raise `--max-pages`" not in summary
