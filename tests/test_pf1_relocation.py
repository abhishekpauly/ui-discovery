"""PF1 — an element must still be findable when the probe reaches it.

A real capture of an authenticated portal, deep-nav on, produced this on the
screen the run existed to document:

    stats: {"elements_seen": 47, "executed": 0, "observed_only": 47,
            "blocked": 1, "states_captured": 0}

    menu      SAFE  exec=False  skip=element not locatable  'Switch space'
    expander  SAFE  exec=False  skip=element not locatable  'All families'

Not one candidate refused on safety, not one over budget. The page had been
re-rendered under the probe and every positional `dom_path` had gone stale.

Elements already carry a generous signal set so identity can be recomputed
later without re-crawling (principle #5). These tests are that promise, kept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_discovery.interactions import probe_page, relocation_strategies
from ui_discovery.models import Interaction

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


# --- the strategy list, without a browser -----------------------------------

def test_dom_path_is_still_tried_first():
    """When the DOM has not moved, a positional path is exact and unambiguous.
    Demoting it would trade a precise answer for a fuzzy one on every page that
    never had this problem."""
    got = relocation_strategies(
        Interaction(dom_path="div > button", target="Save", role="button"))
    assert got[0]["how"] == "dom_path"


def test_role_and_name_are_the_fallback():
    got = relocation_strategies(
        Interaction(dom_path="div > button", target="Save", role="button"))
    assert [s["how"] for s in got] == ["dom_path", "role+name"]
    assert got[1]["role"] == "button" and got[1]["name"] == "Save"


def test_an_implicit_role_is_still_a_role():
    """A <button> carries no role attribute. Refusing to fall back for the most
    common control on the web would leave the fallback almost never used."""
    got = relocation_strategies(
        Interaction(dom_path="div > button", target="Save", category="button"))
    assert any(s["how"] == "role+name" for s in got)


def test_a_testid_is_the_last_resort():
    got = relocation_strategies(
        Interaction(dom_path="div > button", target="Save", role="button"),
        {"attributes": {"data-testid": "save-btn"}})
    assert [s["how"] for s in got] == ["dom_path", "role+name", "testid"]
    assert got[2]["selector"] == '[data-testid="save-btn"]'


def test_a_nameless_control_has_no_identity_fallback():
    """An unnamed icon button cannot be re-found by name, and guessing at one
    is how you click the wrong thing. Positional lookup or nothing."""
    got = relocation_strategies(Interaction(dom_path="div > button"))
    assert [s["how"] for s in got] == ["dom_path"]


# --- and through a real browser ---------------------------------------------

@pytest.fixture(scope="module")
def rerendered_probe():
    """The fixture prepends a banner 1.2s after load, shifting every
    `nth-of-type` index without touching a role or a name."""
    return probe_page(fixture_url("interactive/rerender.html"))


def test_a_re_render_no_longer_costs_the_whole_page(rerendered_probe):
    """The regression. On positional lookup alone every one of these is
    `element not locatable`."""
    executed = [i for i in rerendered_probe.interactions if i.executed]
    assert len(executed) >= 3, (
        "a re-render between extraction and probing lost the page; skips were "
        + repr([(i.target, i.skipped_reason)
                for i in rerendered_probe.interactions if not i.executed]))


def test_nothing_was_lost_to_not_locatable(rerendered_probe):
    stale = [i.target for i in rerendered_probe.interactions
             if i.skipped_reason == "element not locatable"]
    assert not stale, f"still unfindable after re-resolution: {stale}"


def test_the_states_behind_them_are_captured_too(rerendered_probe):
    """The cost of the original defect was not the clicks. It was the eleven
    modals, menus and panels that never opened because of them."""
    assert len(rerendered_probe.states) >= 2, (
        [(s.kind, s.trigger_label) for s in rerendered_probe.states])


# --- the whole point: deep-nav must not cost the probe -----------------------

def _executed_per_page(crawl) -> dict[str, int]:
    return {n.url: (n.probe.stats.get("executed", 0) if n.probe else 0)
            for n in crawl.pages}


def test_deep_nav_does_not_reduce_what_the_probe_reaches(serve, tmp_path):
    """The defect, at the level it was found.

    Deep-nav runs before the probe and interacts with the page, so the probe's
    element paths were recorded against a DOM that no longer exists. On the
    real portal this cost every state on two screens — 22 modals, menus and
    panels — while adding one URL, and `summary.md` reported success.

    The counts must not go DOWN when deep-nav is switched on. Finding more is
    fine; finding less means one feature is eating the other.
    """
    import asyncio

    from ui_discovery.crawler import crawl_site

    site = serve("fixtures/interactive")
    start = site.url("rerender.html")

    without = asyncio.run(crawl_site(
        start, max_depth=1, probe=True, deep_nav=False,
        output_dir=str(tmp_path / "off"), screenshots=False))
    with_ = asyncio.run(crawl_site(
        start, max_depth=1, probe=True, deep_nav=True,
        output_dir=str(tmp_path / "on"), screenshots=False))

    off, on = _executed_per_page(without), _executed_per_page(with_)
    regressed = {url: (off[url], on.get(url, 0))
                 for url in off if on.get(url, 0) < off[url]}
    assert not regressed, (
        f"deep-nav cost the probe interactions it otherwise made: {regressed}")
