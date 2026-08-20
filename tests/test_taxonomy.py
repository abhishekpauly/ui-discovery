"""UI type taxonomy — what kind of control something is.

Pure rules over signals the extractor already records. The tests below are
the specification: each asserts a resolution rule, not an implementation.
"""

from __future__ import annotations

import pytest

from ui_discovery.taxonomy import (
    ALL_TYPES,
    CATALOGUE,
    NOT_DETECTABLE,
    classify,
    coverage,
)


def el(tag="div", role=None, name=None, **attrs):
    return {"tag": tag, "role": role, "accessible_name": name,
            "attributes": attrs, "category": "button"}


# --- resolution order --------------------------------------------------------

def test_app_declared_widget_name_wins():
    """`aria-roledescription` is the app naming its own widget — the only
    signal here that knows what the thing is *for*."""
    assert classify(el("div", role="region",
                       **{"aria-roledescription": "carousel"})) == "carousel"


def test_explicit_role_beats_the_element():
    assert classify(el("div", role="tab")) == "tab"
    assert classify(el("span", role="progressbar")) == "progressbar"


def test_implicit_element_role_when_no_role_attribute():
    """The step a `count the role= attributes` approach misses: most pages
    carry their semantics in the elements themselves."""
    for tag, expected in [("nav", "navigation"), ("table", "table"),
                          ("details", "disclosure"), ("progress", "progressbar"),
                          ("canvas", "canvas"), ("meter", "meter"),
                          ("iframe", "iframe"), ("aside", "sidebar")]:
        assert classify(el(tag)) == expected, tag


@pytest.mark.parametrize("input_type,expected", [
    ("range", "slider"), ("file", "file-upload"), ("number", "spinbutton"),
    ("search", "searchbox"), ("date", "date-input"), ("color", "color-picker"),
    ("password", "password-input"), ("checkbox", "checkbox"),
    ("radio", "radio"), ("text", "textbox"),
])
def test_input_type_resolves(input_type, expected):
    assert classify(el("input", type=input_type)) == expected


def test_state_signals_when_nothing_else_identifies_it():
    assert classify(el("div", contenteditable="true")) == "rich-text-editor"
    assert classify(el("div", **{"aria-sort": "ascending"})) == "sortable-column"
    assert classify(el("div", **{"aria-live": "polite"})) == "live-region"
    assert classify(el("div", **{"aria-expanded": "false"})) == "disclosure"


def test_unidentifiable_elements_get_no_type():
    """Better to say nothing than to guess. A bare div is a bare div."""
    assert classify(el("div")) is None
    assert classify(el("span")) is None


# --- refinements a bare role cannot express ----------------------------------

def test_modal_and_drawer_are_distinguished():
    """The distinction matters: one traps focus, the other does not. Other
    inventories fold drawers into 'modals'; the markup tells them apart."""
    assert classify(el("div", role="dialog", **{"aria-modal": "true"})) == "dialog"
    assert classify(el("div", role="dialog")) == "drawer"


def test_breadcrumb_and_pagination_are_navigation_subtypes():
    assert classify(el("nav", **{"aria-label": "Breadcrumb"})) == "breadcrumb"
    assert classify(el("nav", **{"aria-label": "Pagination"})) == "pagination"
    assert classify(el("nav", **{"aria-label": "Primary"})) == "navigation"


def test_link_subtypes():
    assert classify(el("a", href="/x", target="_blank")) == "external-link"
    assert classify(el("a", href="/x", download="")) == "download-link"
    assert classify(el("a", href="/x")) == "link"


# --- the catalogue is a contract --------------------------------------------

def test_every_catalogue_type_is_unique_to_one_group():
    seen: set[str] = set()
    for members in CATALOGUE.values():
        for t in members:
            assert t not in seen, f"{t} appears in two groups"
            seen.add(t)
    assert seen == set(ALL_TYPES)


def test_coverage_splits_found_absent_and_undetectable():
    cov = coverage({"button": 12, "slider": 1, "carousel": 2})
    assert cov["found"] == {"button": 12, "slider": 1}
    # An app-declared widget is a real finding even though it is not ours.
    assert cov["app_declared"] == {"carousel": 2}
    assert "tab" in cov["absent"]
    assert cov["found_count"] == 2
    assert cov["catalogue_size"] == len(ALL_TYPES)


def test_undetectable_types_are_named_with_reasons():
    """"We found no cards" and "we cannot detect cards" are different
    statements, and only one of them is about the application."""
    assert "card" in NOT_DETECTABLE
    assert "no standard markup" in NOT_DETECTABLE["card"]
    for reason in NOT_DETECTABLE.values():
        assert reason and reason[0].islower()


def test_coverage_of_nothing_reports_everything_absent():
    cov = coverage({})
    assert cov["found"] == {}
    assert len(cov["absent"]) == len(ALL_TYPES)


# --- end to end --------------------------------------------------------------

def test_types_are_recognised_on_a_real_page():
    """Against a fixture carrying one of each kind, through the real
    extractor — so the JS selectors and the Python rules agree."""
    from pathlib import Path

    from ui_discovery.extraction import extract_page

    url = (Path(__file__).resolve().parents[1]
           / "fixtures" / "taxonomy.html").resolve().as_uri()
    page = extract_page(url)
    found = {e.ui_type for e in page.elements if e.ui_type}

    for expected in ("breadcrumb", "navigation", "sidebar", "tab", "tablist",
                     "disclosure", "slider", "file-upload", "searchbox",
                     "date-input", "rich-text-editor", "sortable-column",
                     "progressbar", "alert", "status", "dialog", "drawer",
                     "external-link", "download-link", "canvas", "carousel"):
        assert expected in found, f"{expected} not recognised; got {sorted(found)}"


def test_category_still_works_alongside_ui_type():
    """`ui_type` is a second axis, not a replacement — fingerprints and
    selectors still depend on `category`."""
    from pathlib import Path

    from ui_discovery.extraction import extract_page

    url = (Path(__file__).resolve().parents[1]
           / "fixtures" / "taxonomy.html").resolve().as_uri()
    page = extract_page(url)
    assert {e.category for e in page.elements} >= {"link", "input", "table"}
    assert all(e.category for e in page.elements)
