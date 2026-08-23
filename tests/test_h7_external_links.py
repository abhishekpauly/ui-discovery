"""H7 — links that leave the product are recorded, never followed.

An outbound link was dropped without trace. That made two very different
statements about a product indistinguishable in the artifact:

  * *this product has no integrations*, and
  * *we were not authorized past this point*.

The authorization boundary is a fact about the capture and should be visible in
it, not inferred from an absence. It matters more as the engine grows: `M1`
starts reading sitemaps and `G7` now lists every host contacted, so "what does
this product hand off to?" becomes answerable.

Recorded and **never followed** are both load-bearing. The second half is
asserted against `G7`'s egress ledger rather than against the page count,
because a page the crawler declined to *capture* could still have been
*fetched*, and "we never contacted them" is the claim worth proving.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.relations import build_relations
from ui_discovery.reports import build_markdown
from ui_discovery.run import RunContext

OFFSITE = "http://third-party.invalid"

PAGE = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Home</title></head><body>
<main>
  <h1>Orders</h1>
  <a href="second.html">Internal page</a>
  <nav>
    <a href="{OFFSITE}/docs">Vendor documentation</a>
    <a href="{OFFSITE}/status">Vendor status page</a>
  </nav>
  <a href="mailto:support@example.com">Email support</a>
  <a href="#top">Back to top</a>
</main></body></html>"""

SECOND = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Second</title></head><body><main><h1>Second</h1></main></body></html>"""


@pytest.fixture
def site(serve, tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(PAGE, encoding="utf-8")
    (root / "second.html").write_text(SECOND, encoding="utf-8")
    return serve(str(root))


def _crawl(url, out, run=None, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out), run=run,
        options=CrawlOptions(max_pages=5, max_depth=2, screenshots=False,
                             probe=False, **kw),
    ))


def test_an_offsite_link_is_recorded_with_its_label(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path / "out")

    targets = {e.target: e for e in crawl.external_links}
    assert f"{OFFSITE}/docs" in targets, targets
    assert targets[f"{OFFSITE}/docs"].label == "Vendor documentation"
    assert targets[f"{OFFSITE}/docs"].external is True
    assert targets[f"{OFFSITE}/docs"].source == site.url("index.html")


def test_the_region_the_link_sits_in_is_kept(site, tmp_path):
    """"Where is this link?" is half the answer to "should I care about it?"."""
    crawl = _crawl(site.url("index.html"), tmp_path / "out")
    docs = next(e for e in crawl.external_links if e.target.endswith("/docs"))
    assert docs.region == "navigation"


def test_the_offsite_host_is_never_contacted(site, tmp_path):
    """Asserted on the request log, not the page count. A page the crawler
    declined to capture could still have been fetched."""
    run = RunContext.begin(str(tmp_path / "run"), target=site.base)
    _crawl(site.url("index.html"), tmp_path / "out", run=run)

    contacted = {h.host for h in run.manifest().egress.hosts}
    assert "third-party.invalid" not in contacted, contacted


def test_offsite_links_are_not_captured_as_screens(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path / "out")
    captured = {n.url for n in crawl.pages}
    assert not any("third-party.invalid" in u for u in captured), captured
    assert len(captured) == 2  # index + second


def test_non_navigational_schemes_are_not_external_links(site, tmp_path):
    """`mailto:` and a bare `#` anchor leave nothing and go nowhere. Listing
    them as integrations would make the table useless."""
    crawl = _crawl(site.url("index.html"), tmp_path / "out")
    targets = [e.target for e in crawl.external_links]
    assert not any(t.startswith("mailto:") for t in targets), targets
    assert not any("#top" in t for t in targets), targets


def test_each_offsite_target_is_recorded_once(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path / "out")
    targets = [e.target for e in crawl.external_links]
    assert len(targets) == len(set(targets)) == 2


def test_a_site_with_no_outbound_links_records_none(serve, tmp_path):
    """The empty case has to be genuinely empty — that is what makes "no
    integrations" readable as a finding."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "index.html").write_text(SECOND, encoding="utf-8")
    server = serve(str(root))

    crawl = _crawl(server.url("index.html"), tmp_path / "out")
    assert crawl.external_links == []


def test_relations_carries_the_same_edges(site, tmp_path):
    """Passed through rather than re-derived: a second same-site rule here
    would be a second thing to keep in step with the first."""
    crawl = _crawl(site.url("index.html"), tmp_path / "out")
    relations = build_relations(crawl)
    assert [e.target for e in relations.external] == [
        e.target for e in crawl.external_links]


def test_the_report_names_where_the_product_hands_off(site, tmp_path):
    crawl = _crawl(site.url("index.html"), tmp_path / "out")
    markdown = build_markdown(crawl, build_relations(crawl))

    assert "## Leaves the product" in markdown
    assert "never followed" in markdown
    assert "Vendor documentation" in markdown
    assert f"{OFFSITE}/status" in markdown


def test_the_report_stays_silent_when_nothing_leaves(serve, tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "index.html").write_text(SECOND, encoding="utf-8")
    server = serve(str(root))

    crawl = _crawl(server.url("index.html"), tmp_path / "out")
    assert "## Leaves the product" not in build_markdown(
        crawl, build_relations(crawl))


def test_a_subdomain_admitted_by_policy_is_not_external(tmp_path):
    """`H6` and `H7` must agree by construction: a link is external here
    exactly when it is not navigable there."""
    from ui_discovery.util import HOST_LIST, resolve_external_links

    links = [{"href": "http://admin.example.com/x", "label": "Admin"}]
    assert resolve_external_links("http://app.example.com/",
                                  links, "http://app.example.com/") != []
    assert resolve_external_links(
        "http://app.example.com/", links, "http://app.example.com/",
        subdomains=HOST_LIST, subdomain_hosts=("admin.example.com",)) == []
