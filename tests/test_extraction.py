"""V0 tests. The local HTML fixtures are the primary regression surface —
deterministic and fully under our control. Live sites are only ever smoke
tests, never part of this suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_discovery.extract import slug_for
from ui_discovery.extraction import extract_page
from ui_discovery.models import Page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


@pytest.fixture(scope="module")
def static_page() -> Page:
    return extract_page(fixture_url("static.html"))


@pytest.fixture(scope="module")
def spa_page() -> Page:
    return extract_page(fixture_url("spa_like.html"))


@pytest.fixture(scope="module")
def modal_page() -> Page:
    return extract_page(fixture_url("modal.html"))


@pytest.fixture(scope="module")
def table_page() -> Page:
    return extract_page(fixture_url("table.html"))


# --- schema / serialization -------------------------------------------------

def test_schema_version_and_roundtrip(static_page: Page):
    assert static_page.schema_version == "0.1.0"
    dumped = static_page.model_dump()
    assert dumped["schema_version"] == "0.1.0"
    # Model round-trips through plain JSON-able dict.
    Page.model_validate(dumped)


def test_readiness_recorded(static_page: Page):
    assert static_page.readiness.get("body_present") is True
    assert "total_wait_ms" in static_page.readiness


# --- static page ------------------------------------------------------------

def test_static_headings(static_page: Page):
    texts = [h.text for h in static_page.headings]
    assert "Product Catalog" in texts
    assert any(h.level == 1 for h in static_page.headings)


def test_static_nav_and_links(static_page: Page):
    links = [e for e in static_page.elements if e.category == "link"]
    hrefs = {e.attributes.get("href") for e in links}
    assert "/docs" in hrefs
    # The <a role="button"> is claimed as a button, not a link.
    buttons = [e for e in static_page.elements if e.category == "button"]
    assert any(b.accessible_name == "Open API reference" for b in buttons)


def test_static_image_alt_becomes_accessible_name(static_page: Page):
    images = [e for e in static_page.elements if e.category == "image"]
    assert images
    assert images[0].accessible_name == "Architecture diagram"
    assert images[0].accessible_name_source == "alt"


def test_landmark_captured(static_page: Page):
    links = [e for e in static_page.elements if e.category == "link"]
    assert any(e.landmark in {"navigation", "banner"} for e in links)


# --- SPA-like page (client-rendered after load) -----------------------------

def test_spa_content_is_observed_after_render(spa_page: Page):
    # Nothing is in the initial HTML; extractor must see the injected DOM.
    names = {e.accessible_name for e in spa_page.elements}
    assert "Create customer" in names
    inputs = [e for e in spa_page.elements if e.category == "input"]
    assert any(e.accessible_name == "Search" for e in inputs)


def test_spa_disabled_button(spa_page: Page):
    export = [
        e for e in spa_page.elements
        if e.category == "button" and (e.text or "").strip() == "Export"
    ]
    assert export and export[0].enabled is False


# --- modal page: visibility discrimination ----------------------------------

def test_modal_visibility(modal_page: Page):
    dialogs = [e for e in modal_page.elements if e.category == "dialog"]
    names = {e.accessible_name: e.visible for e in dialogs}
    assert names.get("Confirm deletion") is True
    assert names.get("Hidden helper dialog") is False

    hidden_btns = [
        e for e in modal_page.elements
        if e.text == "Should be marked not-visible"
    ]
    assert hidden_btns and hidden_btns[0].visible is False


# --- table page -------------------------------------------------------------

def test_table_detected(table_page: Page):
    assert table_page.counts.get("table", 0) >= 1


def test_identity_signals_present(table_page: Page):
    view_links = [
        e for e in table_page.elements
        if e.category == "link" and e.text == "View"
    ]
    assert len(view_links) == 2
    # Two same-named links must be distinguishable by identity signals.
    assert view_links[0].dom_path != view_links[1].dom_path
    assert {e.attributes.get("href") for e in view_links} == {
        "/orders/1001",
        "/orders/1002",
    }


# --- slug helper ------------------------------------------------------------

def test_slug_for():
    assert slug_for("https://example.com/docs/api") == "example.com_docs_api"
    assert slug_for("https://example.com") == "example.com"
    assert slug_for("file:///tmp/table.html") == "file_table"
