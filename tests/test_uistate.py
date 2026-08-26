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
from ui_discovery.interactions import backfill_control_options, probe_page
from ui_discovery.uistate import (
    classify_state,
    common_ancestor,
    component_targets,
    option_labels,
    revealed_elements,
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


# --- deduplication (found by running against a real portal) -----------------
#
# On a real portal's model hub, a grid of model cards each carried a "Try out"
# button opening the same Model Playground drawer. The engine photographed it
# 37 times and the report listed all 37. Across that capture, 158 captured
# states collapsed to 14 distinct affordances.

def test_the_same_state_opened_from_many_cards_is_captured_once():
    from ui_discovery.interactions import _remember_state
    from ui_discovery.models import Element, UIState

    def playground(model):
        # Each card's drawer shows that card's own data. It is the same
        # component either way, which is exactly why the revealed contents
        # must not be part of a labelled trigger's identity.
        return UIState(
            kind="drawer", name="Model Playground", trigger_label="Try out",
            page_url="u",
            controls=[Element(category="button", tag="button",
                              accessible_name=n)
                      for n in ("Send", "Attach", model)],
        )

    states: list[UIState] = []
    for i in range(37):
        state = playground(f"GPT 5 variant {i}")
        if _remember_state(states, state):
            states.append(state)

    assert len(states) == 1, "one affordance should be captured once"
    assert states[0].instances == 37


def test_different_triggers_are_different_states():
    from ui_discovery.interactions import _remember_state
    from ui_discovery.models import UIState

    states: list[UIState] = []
    for label in ("Overview", "Activity", "History"):
        state = UIState(kind="tab-panel", trigger_label=label, page_url="u")
        if _remember_state(states, state):
            states.append(state)
    assert len(states) == 3


def test_unlabelled_triggers_are_told_apart_by_what_they_reveal():
    """Icon-only buttons all have an empty trigger label, and they open
    genuinely different menus. Keying on the trigger alone would collapse
    them into one and lose them."""
    from ui_discovery.interactions import _remember_state
    from ui_discovery.models import Element, UIState

    def menu(*names):
        return UIState(
            kind="menu", trigger_label="", page_url="u",
            controls=[Element(category="button", tag="button",
                              accessible_name=n) for n in names],
        )

    states: list[UIState] = []
    for state in (menu("Rename", "Duplicate"), menu("Export", "Print"),
                  menu("Rename", "Duplicate")):
        if _remember_state(states, state):
            states.append(state)
    assert len(states) == 2
    assert states[0].instances == 2


def test_a_container_with_no_name_does_not_get_a_wall_of_text_for_one():
    """A drawer's textContent is everything inside it. Using it as a name
    produced headings like "What's New (V2.14.0)Version 2.14.0Aug 10, 2026
    What's New in ACME We've been busy. Here's everything that landed
    recentl" — unreadable, and it became the image alt text too."""
    blob = ("What's New (V2.14.0)Version 2.14.0Aug 10, 2026What's New in "
            "ACME We've been busy. Here's everything that landed recently")
    drawer = el(attributes={"role": "dialog"}, text=blob, dom_path="div#d")
    trigger = el(category="button", tag="button",
                 accessible_name="What's New (V2.14.0)")

    found = classify_state(trigger, [drawer])
    assert found["name"] == "What's New (V2.14.0)", found["name"]
    assert len(found["name"]) <= 60


def test_a_short_text_name_is_still_used():
    drawer = el(attributes={"role": "dialog"}, text="Filters", dom_path="div#d")
    trigger = el(category="button", tag="button", accessible_name="Open")
    assert classify_state(trigger, [drawer])["name"] == "Filters"


# --- tabs the way component libraries actually build them -------------------
#
# `interactive/index.html` toggles `hidden` on two panels that are in the DOM
# from the start. Radix, Headless UI and MUI commonly do neither: the inactive
# panel is unmounted and the active one created, and generated ids contain
# characters that need CSS escaping, so `aria-controls` and `dom_path` are not
# the same string. Nothing covered that shape until a real portal raised the
# question.


@pytest.fixture(scope="module")
def mounted_tabs_probe(tmp_path_factory):
    states = tmp_path_factory.mktemp("mounted-tab-states")
    probe = probe_page(
        fixture_url("interactive/tabs-mounted.html"),
        states_dir=str(states),
        capture_states=True,
    )
    return probe, states


def test_a_panel_that_is_mounted_on_switch_is_still_captured(mounted_tabs_probe):
    probe, _ = mounted_tabs_probe
    panels = [s for s in probe.states if s.kind == "tab-panel"]
    assert panels, f"no tab panel captured; got {[s.kind for s in probe.states]}"


def test_an_escaped_id_still_resolves_to_its_panel(mounted_tabs_probe):
    r"""`aria-controls="radix-:r1:-content-instructions"` becomes
    `div#radix-\:r1\:-content-instructions` in `dom_path`. If those two are
    ever compared without escaping, every panel silently stops resolving."""
    probe, _ = mounted_tabs_probe
    panels = [s for s in probe.states if s.kind == "tab-panel"]
    assert any("radix" in (s.dom_path or "") for s in panels), (
        f"panels resolved to {[s.dom_path for s in panels]}")


def test_each_switched_tab_is_its_own_state(mounted_tabs_probe):
    """Three tabs, distinct panels. Collapsing them would report a config
    screen as having one section when it has three."""
    probe, _ = mounted_tabs_probe
    labels = {s.trigger_label for s in probe.states if s.kind == "tab-panel"}
    assert len(labels) >= 2, f"only these tabs opened anything: {labels}"


# --- PF4: a state the engine opened should have its contents in the model ---
#
# One real capture recorded 170 states: menus 53/53 with contents, drawers
# 23/57, and disclosures 0/60. Not one disclosure in the whole run recorded a
# single control, so two filter dropdowns that were opened AND photographed
# reported `option_count = 0` and their values existed only as pixels.
#
# The cause was the container, not the click. Contents are found by dom_path
# prefix, and the `aria-expanded` branch credited the state with the SHALLOWEST
# REVEALED ELEMENT — which contains its siblings not at all, and contains
# nothing whatsoever when it is a leaf.


def test_common_ancestor_of_siblings_is_their_parent():
    assert common_ancestor(["div#p > button:nth-of-type(1)",
                            "div#p > button:nth-of-type(2)"]) == "div#p"


def test_common_ancestor_prefers_a_path_that_contains_the_others():
    """An accordion that reveals its panel AND the buttons in it should be
    credited with the panel, not with the panel's parent."""
    assert common_ancestor(["div#p",
                            "div#p > button:nth-of-type(1)"]) == "div#p"


def test_common_ancestor_never_cuts_mid_selector():
    """`div:nth-of-type(1)` and `div:nth-of-type(2)` share the characters
    `div:nth-of-type(`, which is not a selector and would match neither."""
    got = common_ancestor(["main > div:nth-of-type(1) > a",
                           "main > div:nth-of-type(2) > a"])
    assert got == "main"


def test_common_ancestor_of_disjoint_subtrees_is_nothing():
    """A panel that expands inline and a listbox portaled to the body root have
    no common container. Saying so beats inventing one that matches everything
    — `startswith("")` is true of every path on the page."""
    assert common_ancestor(["div#root > div", "div#portal > div"]) == ""


def test_a_disclosure_is_credited_with_what_contains_its_contents():
    """The regression, as a unit. Two controls appear as siblings inside a
    panel; the state must be the panel, so both are its contents."""
    trigger = el(category="button", tag="button", accessible_name="Anthropic",
                 attributes={"aria-expanded": "true"}, dom_path="button#t")
    revealed = [
        el(category="button", tag="button", accessible_name="Claude Opus",
           dom_path="div#acc-panel > button:nth-of-type(1)"),
        el(category="button", tag="button", accessible_name="Claude Sonnet",
           dom_path="div#acc-panel > button:nth-of-type(2)"),
    ]
    found = classify_state(trigger, revealed)
    assert found["kind"] == "disclosure"
    assert found["dom_path"] == "div#acc-panel", (
        "the container has to CONTAIN the contents; the shallowest revealed "
        "element is a sibling of them and holds nothing")


def test_option_labels_reads_a_revealed_choice_list():
    revealed = [
        el(category="option", tag="div", attributes={"role": "option"},
           accessible_name="Active", dom_path="div#l > div:nth-of-type(1)"),
        el(category="option", tag="div", attributes={"role": "option"},
           accessible_name="Deprecated", dom_path="div#l > div:nth-of-type(2)"),
        el(category="button", tag="button", accessible_name="Apply",
           dom_path="div#l > button"),
    ]
    assert option_labels(revealed) == ["Active", "Deprecated"], (
        "a button in the dropdown is not one of the choices")


def test_backfill_never_overwrites_what_a_control_reported_itself():
    """A native <select> answered for itself. Observation beats
    reconstruction, and the reconstruction is the weaker source."""
    from types import SimpleNamespace

    from ui_discovery.models import Element, Option, UIState

    native = Element(category="select", tag="select", dom_path="select#s",
                     options=[Option(label="Yes")], option_count=1)
    page = SimpleNamespace(elements=[native])
    state = UIState(kind="listbox", trigger_path="select#s",
                    options=["Something", "Else"])

    assert backfill_control_options(page, [state]) == 0
    assert [o.label for o in page.elements[0].options] == ["Yes"]


def test_backfill_gives_a_portaled_combobox_the_options_it_could_not_report():
    from types import SimpleNamespace

    from ui_discovery.models import Element, UIState

    combo = Element(category="button", tag="button", dom_path="button#status",
                    accessible_name="All statuses")
    page = SimpleNamespace(elements=[combo])
    state = UIState(kind="listbox", trigger_path="button#status",
                    options=["All statuses", "Active", "Deprecated"])

    assert backfill_control_options(page, [state]) == 1
    assert page.elements[0].option_count == 3
    assert [o.label for o in page.elements[0].options][1] == "Active"


# --- and the same thing through a real browser ------------------------------

@pytest.fixture(scope="module")
def portaled_probe(tmp_path_factory):
    states = tmp_path_factory.mktemp("portaled-states")
    probe = probe_page(
        fixture_url("interactive/portaled.html"),
        states_dir=str(states),
        capture_states=True,
    )
    return probe, states


def test_an_accordion_panel_records_the_controls_it_reveals(portaled_probe):
    probe, _ = portaled_probe
    panels = [s for s in probe.states
              if s.trigger_label == "Anthropic" and s.controls]
    assert panels, (
        "the accordion opened and recorded no contents; states were "
        f"{[(s.kind, s.trigger_label, len(s.controls)) for s in probe.states]}")
    names = {(c.accessible_name or c.text or "").strip()
             for c in panels[0].controls}
    assert "Claude Opus" in names, names


def test_a_portaled_listbox_yields_its_options(portaled_probe):
    """The listbox is a sibling of the app root and exists only while open, so
    `extract.js` cannot see it at extraction time. This is the one moment the
    values are in the DOM."""
    probe, _ = portaled_probe
    with_options = [s for s in probe.states if s.options]
    assert with_options, (
        "no state recorded any options; "
        f"{[(s.kind, s.trigger_label) for s in probe.states]}")
    assert any("Deprecated" in s.options for s in with_options), (
        [s.options for s in with_options])


def test_no_state_kind_is_systematically_empty(portaled_probe):
    """The shape of the original defect: disclosures at 0/60 while menus were
    at 53/53. A kind that never records anything is a bug, not a fact about
    the page."""
    probe, _ = portaled_probe
    by_kind: dict[str, list[bool]] = {}
    for state in probe.states:
        has = bool(state.controls or state.options or state.headings)
        by_kind.setdefault(state.kind, []).append(has)
    empty = [k for k, hits in by_kind.items() if len(hits) > 1 and not any(hits)]
    assert not empty, f"these kinds recorded nothing, ever: {empty}"
