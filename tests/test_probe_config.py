"""Probing is configured per module and per tab, not by one global switch.

A single "probe: on/off" is the wrong shape for a real portal. You want the
Orders module exercised thoroughly, the Reports module read but never clicked,
and — inside a module — only the tabs that matter opened, because opening the
Audit Log tab on sixty screens is sixty pointless clicks and sixty pointless
screenshots.

Two rules these tests exist to hold:

  * Precedence is **flags > module > top-level `probe:` > capabilities**, the
    same rule the rest of the config follows.
  * The tab policy can only ever *narrow* what gets clicked. Nothing in a
    config file can talk the engine into clicking something the safety gates
    refuse.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest

from ui_discovery.cliconfig import crawl_options, probe_profile, probe_rules
from ui_discovery.config import Scope
from ui_discovery.crawler import crawl_site
from ui_discovery.interactions import ProbeProfile, probe_page, tab_allowed
from ui_discovery.util import module_for_path, path_of

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def args(**kw) -> argparse.Namespace:
    base = dict(
        probe=None, no_probe=None, no_state_capture=None,
        no_component_screenshots=None, max_interactions=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


# --- resolution -------------------------------------------------------------

def test_defaults_probe_the_whole_product():
    """Probing is on by default: a capture that never clicks anything cannot
    see a modal, a menu, a tab panel or an API call."""
    profile = probe_profile(Scope(), args())
    assert profile.enabled is True
    assert profile.tabs == "all"
    assert profile.state_capture is True
    assert profile.component_screenshots is True


def test_a_module_overrides_the_top_level_block():
    scope = Scope.model_validate({
        "probe": {"max_interactions": 10, "tabs": "all"},
        "modules": [{"name": "Orders", "start_url": "/orders",
                     "probe": {"max_interactions": 60, "tabs": "listed",
                               "tab_labels": ["Overview"]}}],
    })
    default = probe_profile(scope, args())
    orders = probe_profile(scope, args(), scope.modules[0].probe)

    assert default.max_interactions == 10
    assert orders.max_interactions == 60
    assert orders.tabs == "listed"
    assert orders.tab_labels == ("Overview",)


def test_a_module_inherits_what_it_does_not_state():
    scope = Scope.model_validate({
        "probe": {"state_capture": False, "max_interactions": 12},
        "modules": [{"name": "Orders", "start_url": "/orders",
                     "probe": {"max_interactions": 60}}],
    })
    orders = probe_profile(scope, args(), scope.modules[0].probe)
    assert orders.max_interactions == 60      # its own
    assert orders.state_capture is False      # inherited


def test_the_top_level_block_falls_back_to_capabilities():
    """`capabilities.probe` stays the master switch, so existing configs that
    only set it keep working and no key becomes dead."""
    scope = Scope.model_validate({"capabilities": {"probe": False}})
    assert probe_profile(scope, args()).enabled is False


def test_max_interactions_falls_back_to_the_budget():
    scope = Scope.model_validate({"budget": {"max_interactions": 7}})
    assert probe_profile(scope, args()).max_interactions == 7


def test_a_module_can_be_read_but_never_clicked():
    scope = Scope.model_validate({
        "modules": [{"name": "Reports", "start_url": "/reports",
                     "probe": {"enabled": False}}],
    })
    assert probe_profile(scope, args(), scope.modules[0].probe).enabled is False
    assert probe_profile(scope, args()).enabled is True


# --- flags win --------------------------------------------------------------

def test_no_probe_overrides_a_config_that_enables_it():
    scope = Scope.model_validate({
        "capabilities": {"probe": True},
        "modules": [{"name": "Orders", "start_url": "/orders",
                     "probe": {"enabled": True}}],
    })
    assert probe_profile(scope, args(no_probe=True)).enabled is False
    for _, rule in probe_rules(scope, args(no_probe=True)):
        assert rule.enabled is False


def test_probe_flag_overrides_a_config_that_disables_it():
    scope = Scope.model_validate({"capabilities": {"probe": False}})
    assert probe_profile(scope, args(probe=True)).enabled is True


def test_flags_can_turn_off_the_two_screenshot_passes():
    scope = Scope()
    p = probe_profile(scope, args(no_state_capture=True,
                                  no_component_screenshots=True))
    assert p.state_capture is False
    assert p.component_screenshots is False
    assert p.enabled is True, "the probe itself should still run"


# --- module matching --------------------------------------------------------

def test_the_most_specific_module_wins():
    rules = [("/platform", "broad"), ("/platform/rag/containers", "narrow")]
    assert module_for_path("/platform/rag/containers/x", rules) == "narrow"
    assert module_for_path("/platform/other", rules) == "broad"
    assert module_for_path("/elsewhere", rules, default="none") == "none"


def test_probe_rules_are_keyed_by_url_path():
    scope = Scope.model_validate({
        "modules": [{"name": "Orders", "start_url": "https://x.test/orders",
                     "probe": {"tabs": "none"}}],
    })
    rules = probe_rules(scope, args())
    assert [prefix for prefix, _ in rules] == ["/orders"]
    assert module_for_path(path_of("https://x.test/orders/1001"),
                           list(rules)).tabs == "none"


def test_module_assignment_and_probe_resolution_agree():
    """A page must never be probed with one module's settings and filed under
    another's — so both use the same longest-prefix matcher."""
    from ui_discovery.inventory import assign_modules
    from ui_discovery.models import Crawl, CrawlConfig, CrawlStats, Page, PageNode

    def node(url):
        page = Page(schema_version="0.1.0", engine_version="0",
                    extracted_at="", requested_url=url, final_url=url, title="")
        return PageNode(url=url, page=page)

    urls = ["https://x.test/orders/1001", "https://x.test/reports/a",
            "https://x.test/other"]
    crawl = Crawl(
        schema_version="0.1.0", engine_version="0", crawl_id="c",
        started_at="", finished_at="",
        config=CrawlConfig(start_url="https://x.test/", max_pages=1,
                           max_depth=1, strategy="same-domain"),
        stats=CrawlStats(pages_crawled=3, pages_failed=0, unique_urls=3,
                         links_discovered=0, runtime_seconds=0.0),
        pages=[node(u) for u in urls],
    )
    modules = [("Orders", "https://x.test/orders"),
               ("Reports", "https://x.test/reports")]
    grouped = assign_modules(crawl, modules)
    folder_of = {n.url: folder for folder, nodes in grouped.items() for n in nodes}

    rules = [("/orders", "Orders"), ("/reports", "Reports")]
    for url in urls:
        by_probe = module_for_path(path_of(url), rules, default="general")
        assert folder_of[url] == by_probe, url


# --- tab policy -------------------------------------------------------------

def test_tabs_all_opens_everything():
    assert tab_allowed("Anything", ProbeProfile(tabs="all"))


def test_tabs_none_opens_nothing():
    assert not tab_allowed("Overview", ProbeProfile(tabs="none"))


def test_tabs_listed_opens_only_what_is_named():
    p = ProbeProfile(tabs="listed", tab_labels=("Overview", "Activity"))
    assert tab_allowed("Overview", p)
    assert tab_allowed("Activity", p)
    assert not tab_allowed("Audit Log", p)


def test_tab_matching_is_forgiving_about_case_and_spacing():
    """A config is written by someone reading the screen, not the DOM."""
    p = ProbeProfile(tabs="listed", tab_labels=("Order  History",))
    assert tab_allowed("order history", p)


def test_tab_exclude_wins_over_everything():
    p = ProbeProfile(tabs="listed", tab_labels=("Audit Log", "Overview"),
                     tab_exclude=("Audit Log",))
    assert not tab_allowed("Audit Log", p)
    assert tab_allowed("Overview", p)
    assert not tab_allowed("Audit Log", ProbeProfile(tab_exclude=("Audit Log",)))


def test_the_tab_policy_can_only_narrow_never_widen():
    """There is no setting that makes the engine click something the safety
    gates refuse — the same rule as SafetyPolicy, which has no way to remove a
    block word."""
    from ui_discovery.config import ProbeSettings

    assert not hasattr(ProbeSettings(), "allow_list")
    assert not hasattr(ProbeSettings(), "block_words_remove")
    with pytest.raises(ValueError):
        ProbeSettings(tabs="everything")


# --- against a real page ----------------------------------------------------

def test_excluded_tabs_are_recorded_as_skipped_not_omitted():
    """A reader must be able to tell "this tab was not opened" from "this tab
    does not exist"."""
    probe = probe_page(fixture_url("interactive/index.html"),
                       profile=ProbeProfile(tabs="none"))
    tabs = [i for i in probe.interactions if i.interaction_type == "tab"]
    assert tabs, "the fixture has tabs"
    assert all(not t.executed for t in tabs)
    assert all(t.skipped_reason == "tab excluded by config" for t in tabs)
    assert not [s for s in probe.states if s.kind == "tab-panel"]


def test_a_listed_tab_still_opens_and_is_captured():
    probe = probe_page(
        fixture_url("interactive/index.html"),
        profile=ProbeProfile(tabs="listed", tab_labels=("Activity",)),
    )
    executed = {i.target for i in probe.interactions
                if i.interaction_type == "tab" and i.executed}
    assert "Activity" in executed
    assert "Overview" not in executed


def test_the_probe_records_the_profile_it_ran_under():
    profile = ProbeProfile(tabs="listed", tab_labels=("Activity",),
                           tab_exclude=("Audit Log",))
    probe = probe_page(fixture_url("interactive/index.html"), profile=profile)
    recorded = probe.config["profile"]
    assert recorded["tabs"] == "listed"
    assert recorded["tab_labels"] == ["Activity"]
    assert recorded["tab_exclude"] == ["Audit Log"]


# --- end to end -------------------------------------------------------------

def test_a_crawl_probes_one_module_and_not_another(tmp_path):
    from tests.conftest import Server

    server = Server(FIXTURES / "forms")
    try:
        base = server.base
        scope = Scope.model_validate({
            "modules": [
                {"name": "Orders", "start_url": f"{base}/orders.html",
                 "probe": {"enabled": False}},
            ],
        })
        options = crawl_options(scope, args()).replace(
            max_pages=5, max_depth=2, headless=True)
        crawl = asyncio.run(crawl_site(
            f"{base}/index.html", output_dir=str(tmp_path), options=options))
    finally:
        server.stop()

    by_url = {n.url: n for n in crawl.pages}
    intake = next(n for u, n in by_url.items() if u.endswith("index.html"))
    orders = next(n for u, n in by_url.items() if u.endswith("orders.html"))

    assert intake.probe is not None, "the default profile should probe"
    assert orders.probe is None, "the Orders module opted out of probing"
    # The page itself is still fully captured — opting out of clicking is not
    # opting out of the capture.
    assert orders.page.elements
    assert orders.page.title == "Orders"


def test_the_snapshot_records_which_areas_were_probed(tmp_path):
    from tests.conftest import Server

    server = Server(FIXTURES / "forms")
    try:
        base = server.base
        scope = Scope.model_validate({
            "modules": [{"name": "Orders", "start_url": f"{base}/orders.html",
                         "probe": {"enabled": False, "tabs": "none"}}],
        })
        options = crawl_options(scope, args()).replace(
            max_pages=3, max_depth=1, headless=True)
        crawl = asyncio.run(crawl_site(
            f"{base}/index.html", output_dir=str(tmp_path), options=options))
    finally:
        server.stop()

    profiles = crawl.config.probe_profiles
    assert profiles, "the capture does not say how it probed"
    scopes = {p["scope"] for p in profiles}
    assert "(default)" in scopes
    assert "/orders.html" in scopes
    orders = next(p for p in profiles if p["scope"] == "/orders.html")
    assert orders["enabled"] is False
    assert orders["tabs"] == "none"


def test_the_report_says_what_it_chose_not_to_open(tmp_path):
    """A tab nobody opened must not read as a tab that does not exist."""
    from tests.conftest import Server
    from ui_discovery.reports import build_html, build_markdown

    server = Server(FIXTURES / "interactive")
    try:
        base = server.base
        scope = Scope.model_validate({
            "probe": {"tabs": "listed", "tab_labels": ["Overview"],
                      "tab_exclude": ["Activity"]},
        })
        options = crawl_options(scope, args()).replace(
            max_pages=2, max_depth=0, headless=True)
        crawl = asyncio.run(crawl_site(
            f"{base}/index.html", output_dir=str(tmp_path), options=options))
    finally:
        server.stop()

    md = build_markdown(crawl)
    assert "Only these tabs were opened" in md
    assert "Overview" in md
    assert "explicitly never opened" in md
    # And the specific controls it declined, by name.
    assert "Tabs present but not opened, by configuration" in md
    assert "“Activity”" in md

    page = build_html(crawl)
    assert "Only these tabs were opened" in page
