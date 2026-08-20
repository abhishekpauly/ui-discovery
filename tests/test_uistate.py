"""Pictures of the parts of a product a settled screenshot cannot show.

A full-page shot of a URL captures what is always there. The modal behind "Add
customer", the menu behind the overflow button, the panel behind the second
tab — none of those are on the page until something is clicked, and those are
usually where the product actually is.

Two capture paths are covered here:

  * component crops — forms, dialogs, tab panels and tables on a settled page,
    each cropped to itself. No clicking.
  * revealed states — what a *probed* click opened. Rides entirely on clicks
    the probe already makes; it introduces no interaction of its own, and the
    safety gates are untouched (a "Delete account" button still opens nothing,
    because it is still never clicked).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ui_discovery.crawler import crawl_site
from ui_discovery.interactions import build_state, probe_page
from ui_discovery.uistate import (
    classify_state,
    component_targets,
    revealed_elements,
    visible_paths,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


# --- pure classification ----------------------------------------------------

def el(**kw) -> dict:
    base = {
        "category": "dialog", "tag": "div", "role": None,
        "accessible_name": None, "text": None, "visible": True,
        "attributes": {}, "dom_path": "div", "bounding_box": {
            "x": 0, "y": 0, "width": 300, "height": 200},
        "controls": [],
    }
    base.update(kw)
    return base


def test_a_modal_and_a_drawer_are_told_apart():
    """One traps focus and one does not — the distinction is `aria-modal`, and
    it is the difference between two different UI patterns."""
    modal = el(attributes={"role": "dialog", "aria-modal": "true"},
               accessible_name="Confirm", dom_path="div#m")
    drawer = el(attributes={"role": "dialog"},
                accessible_name="Filters", dom_path="div#d")
    trigger = el(category="button", tag="button", accessible_name="Open")

    assert classify_state(trigger, [modal])["kind"] == "modal"
    assert classify_state(trigger, [drawer])["kind"] == "drawer"


def test_aria_controls_beats_guessing():
    """When the app says which element a control opens, that is the answer."""
    panel = el(category="tab", attributes={"role": "tabpanel"},
               accessible_name="Activity", dom_path="section#p-activity")
    noise = el(category="menu", attributes={"role": "menu"},
               dom_path="ul#unrelated")
    trigger = el(category="button", tag="button", accessible_name="Activity",
                 controls=["section#p-activity"])

    found = classify_state(trigger, [noise, panel])
    assert found["kind"] == "tab-panel"
    assert found["dom_path"] == "section#p-activity"


def test_the_outermost_container_is_the_state_not_its_contents():
    """A dialog and the buttons inside it all appear at once. The dialog is
    the state; the buttons are what is in it."""
    dialog = el(attributes={"role": "dialog", "aria-modal": "true"},
                accessible_name="Confirm", dom_path="body > div")
    inner = el(category="dialog", attributes={"role": "dialog"},
               dom_path="body > div > section > div")
    trigger = el(category="button", tag="button", accessible_name="Open")

    assert classify_state(trigger, [inner, dialog])["dom_path"] == "body > div"


def test_a_change_that_is_not_a_nameable_state_produces_none():
    """A table re-sorting is a real outcome, already recorded on the
    Interaction. Inventing a "state" for it would fill the report with
    pictures of nothing."""
    trigger = el(category="button", tag="button", accessible_name="Sort")
    assert classify_state(trigger, []) is None
    plain = el(category="button", tag="button", dom_path="button#x",
               attributes={})
    assert classify_state(trigger, [plain]) is None


def test_an_expanded_disclosure_is_recognised_through_its_trigger():
    """A plain <div> panel behind an aria-expanded button has no role of its
    own, but the trigger names the pattern."""
    panel = el(category="region", tag="div", dom_path="main > div",
               attributes={})
    trigger = el(category="button", tag="button", accessible_name="Details",
                 attributes={"aria-expanded": "false"})
    found = classify_state(trigger, [panel])
    assert found["kind"] == "disclosure"
    assert found["name"] == "Details"


def test_revealed_elements_is_a_visibility_diff():
    """Most dialogs are already in the DOM and merely hidden, so "appeared"
    has to mean "became visible", not "was added"."""
    before = {"a", "b"}
    after = {"elements": [
        el(dom_path="a"), el(dom_path="b"), el(dom_path="c"),
        el(dom_path="d", visible=False),
    ]}
    assert [e["dom_path"] for e in revealed_elements(before, after)] == ["c"]


# --- component targets ------------------------------------------------------

def test_forms_and_tables_are_worth_their_own_picture():
    raw = {"elements": [
        el(category="form", tag="form", dom_path="form#a",
           accessible_name="New order"),
        el(category="table", tag="table", dom_path="main > table",
           accessible_name="Recent orders"),
    ]}
    kinds = {t["kind"] for t in component_targets(raw)}
    assert kinds == {"form", "table"}


def test_icons_and_full_page_containers_are_not_cropped():
    """A 20px box is an icon; a 9000px one is the page, which the full-page
    screenshot already covers."""
    raw = {"elements": [
        el(category="form", tag="form", dom_path="form#tiny",
           accessible_name="Tiny", bounding_box={"x": 0, "y": 0,
                                                 "width": 20, "height": 20}),
        el(category="form", tag="form", dom_path="form#huge",
           accessible_name="Huge", bounding_box={"x": 0, "y": 0,
                                                 "width": 900, "height": 9000}),
    ]}
    assert component_targets(raw) == []


def test_a_short_wide_filter_bar_is_still_a_component():
    """An icon is small in *both* directions; the width floor is what excludes
    them. An unstyled single-row filter bar measures 1264x21 in a real render —
    a square-ish size floor silently dropped exactly that case."""
    raw = {"elements": [
        el(category="form", tag="form", dom_path="form#filters",
           accessible_name="Filter orders",
           bounding_box={"x": 8, "y": 106, "width": 1264, "height": 21}),
    ]}
    assert [t["name"] for t in component_targets(raw)] == ["Filter orders"]


def test_an_unnamed_region_is_not_cropped():
    """An unnamed region is a <div>. A picture of it could not be labelled."""
    raw = {"elements": [
        el(category="region", tag="aside", dom_path="aside", attributes={}),
    ]}
    assert component_targets(raw) == []


def test_hidden_components_are_not_cropped():
    raw = {"elements": [
        el(category="form", tag="form", dom_path="form#h",
           accessible_name="Hidden", visible=False),
    ]}
    assert component_targets(raw) == []


# --- against a real page ----------------------------------------------------

@pytest.fixture(scope="module")
def interactive_probe(tmp_path_factory):
    """Probe the interactive fixture, writing state screenshots to disk."""
    states = tmp_path_factory.mktemp("states")
    probe = probe_page(
        fixture_url("interactive/index.html"),
        states_dir=str(states),
        capture_states=True,
    )
    return probe, states


def test_switching_a_tab_captures_the_panel_it_opens(interactive_probe):
    probe, _ = interactive_probe
    panels = [s for s in probe.states if s.kind == "tab-panel"]
    assert panels, f"no tab panel captured; got {[s.kind for s in probe.states]}"
    assert any(s.trigger_label == "Activity" for s in panels), (
        f"the Activity tab opened nothing; triggers were "
        f"{[s.trigger_label for s in panels]}")


def test_opening_a_menu_captures_it_with_its_items(interactive_probe):
    probe, _ = interactive_probe
    menus = [s for s in probe.states if s.kind == "menu"]
    assert menus, f"no menu captured; got {[s.kind for s in probe.states]}"
    labels = {c.accessible_name for s in menus for c in s.controls}
    assert {"Rename", "Duplicate"} <= labels, labels


def test_every_captured_state_has_a_screenshot_on_disk(interactive_probe):
    probe, _ = interactive_probe
    assert probe.states
    for state in probe.states:
        assert state.screenshot, f"{state.kind} state has no screenshot"
        assert Path(state.screenshot).exists()
        assert Path(state.screenshot).stat().st_size > 0


def test_states_are_counted_in_the_probe_stats(interactive_probe):
    probe, _ = interactive_probe
    assert probe.stats["states_captured"] == len(probe.states)


def test_a_refused_control_opens_nothing(interactive_probe):
    """"Delete account" carries `aria-haspopup=dialog`, so its *type* is
    allow-listed — and its label still refuses it. It must therefore appear in
    no captured state, because it was never clicked."""
    probe, _ = interactive_probe
    delete = next(i for i in probe.interactions if i.target == "Delete account")
    assert delete.executed is False
    assert delete.safety_label == "BLOCK"
    assert not [s for s in probe.states if s.trigger_label == "Delete account"]
    assert not [s for s in probe.states if "Confirm deletion" in (s.name or "")]


def test_state_capture_can_be_turned_off():
    probe = probe_page(fixture_url("interactive/index.html"),
                       capture_states=False)
    assert probe.states == []
    # The interactions themselves are unaffected.
    assert probe.stats["executed"] > 0


def test_states_without_a_directory_are_still_recorded(interactive_probe):
    """The data is the point; the picture is a bonus. A run with screenshots
    off still learns what each control opens."""
    probe = probe_page(fixture_url("interactive/index.html"), states_dir=None)
    assert probe.states
    assert all(s.screenshot is None for s in probe.states)
    assert any(s.controls for s in probe.states)


# --- component crops during a crawl -----------------------------------------

def test_a_crawl_crops_the_components_on_each_page(tmp_path):
    from tests.conftest import Server

    server = Server(FIXTURES / "forms")
    try:
        crawl = asyncio.run(crawl_site(
            f"{server.base}/index.html",
            max_pages=3, max_depth=1,
            output_dir=str(tmp_path),
            probe=False,
        ))
    finally:
        server.stop()

    components = tmp_path / "screenshots" / "components"
    assert components.exists(), "no component screenshots were written"
    assert list(components.glob("*.png"))

    cropped = [
        e for node in crawl.pages for e in node.page.elements
        if e.clip_screenshot
    ]
    assert cropped, "no element records its own crop"
    names = {e.accessible_name for e in cropped}
    assert "New order" in names, names
    assert "Recent orders" in names, names
    for element in cropped:
        assert Path(element.clip_screenshot).exists()


def test_component_screenshots_can_be_turned_off(tmp_path):
    from tests.conftest import Server

    server = Server(FIXTURES / "forms")
    try:
        crawl = asyncio.run(crawl_site(
            f"{server.base}/index.html",
            max_pages=2, max_depth=0,
            output_dir=str(tmp_path),
            probe=False,
            component_screenshots=False,
        ))
    finally:
        server.stop()

    assert not (tmp_path / "screenshots" / "components").exists()
    assert not [e for node in crawl.pages for e in node.page.elements
                if e.clip_screenshot]
