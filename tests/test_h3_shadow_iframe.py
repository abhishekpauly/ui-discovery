"""H3 — see inside open shadow roots and same-origin iframes.

Two boundaries the extractor used to be blind to, with deliberately different
policies:

  * **open shadow roots** are traversed — their contents are part of the page
    (component libraries put real controls there). Closed roots are not, and
    cannot be: `element.shadowRoot` is null for them by web standards.
  * **same-origin iframes** are traversed and merged; **cross-origin** ones
    are recorded but not entered. That is a scoping decision, not a technical
    limit — third-party embedded content is outside the product under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_discovery.extraction import extract_page
from ui_discovery.models import Page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def names(page: Page, category: str | None = None) -> set[str]:
    return {
        e.accessible_name for e in page.elements
        if e.accessible_name and (category is None or e.category == category)
    }


# --- shadow DOM -------------------------------------------------------------

@pytest.fixture(scope="module")
def shadow_page() -> Page:
    return extract_page(fixture_url("edge/shadow.html"))


def test_light_dom_still_extracted(shadow_page):
    assert "Light button" in names(shadow_page, "button")


def test_open_shadow_root_contents_are_extracted(shadow_page):
    found = names(shadow_page)
    assert "Shadow button" in found
    assert "Shadow link" in found
    assert "Shadow input" in found


def test_nested_open_shadow_roots_are_traversed(shadow_page):
    assert "Nested shadow button" in names(shadow_page, "button")


def test_closed_shadow_root_is_not_reachable(shadow_page):
    # Not a policy choice — `element.shadowRoot` is null for closed roots.
    assert "Closed shadow button" not in names(shadow_page)


def test_shadow_elements_carry_depth_provenance(shadow_page):
    by_name = {e.accessible_name: e for e in shadow_page.elements}
    assert by_name["Light button"].shadow_depth == 0
    assert by_name["Shadow button"].shadow_depth == 1
    assert by_name["Nested shadow button"].shadow_depth == 2


def test_shadow_dom_path_marks_the_boundary(shadow_page):
    btn = next(e for e in shadow_page.elements
               if e.accessible_name == "Shadow button")
    assert " >>> " in btn.dom_path


def test_visibility_is_correct_inside_shadow_dom(shadow_page):
    hidden = [e for e in shadow_page.elements
              if e.accessible_name == "Hidden shadow button"]
    assert hidden and hidden[0].visible is False


def test_shadow_counts_reported(shadow_page):
    assert shadow_page.counts.get("shadow_dom_elements", 0) >= 4


def test_pages_without_shadow_dom_are_unchanged(serve):
    page = extract_page(fixture_url("static.html"))
    assert all(e.shadow_depth == 0 for e in page.elements)
    assert "shadow_dom_elements" not in page.counts
    assert page.frames == []


# --- iframes ----------------------------------------------------------------

@pytest.fixture
def iframe_page(serve, tmp_path) -> Page:
    """A host page with one same-origin and one genuinely cross-origin iframe.

    Two servers on different ports are two different origins. The host page is
    generated into a tmp dir (served as its own origin) so its cross-origin
    `src` can carry the *other* server's absolute URL.
    """
    from tests.conftest import Server

    third_party = serve("fixtures/edge")  # the other origin

    child = (FIXTURES / "edge" / "iframe_child.html").read_text(encoding="utf-8")
    (tmp_path / "iframe_child.html").write_text(child, encoding="utf-8")

    host_html = (FIXTURES / "edge" / "iframe_host.html").read_text(encoding="utf-8")
    host_html = host_html.replace(
        'id="cross-origin" src="about:blank"',
        f'id="cross-origin" src="{third_party.url("iframe_child.html")}"',
    )
    (tmp_path / "iframe_host.html").write_text(host_html, encoding="utf-8")

    own_origin = Server(tmp_path)
    try:
        return extract_page(own_origin.url("iframe_host.html"))
    finally:
        own_origin.stop()


def test_same_origin_iframe_contents_are_merged(iframe_page):
    found = names(iframe_page)
    assert "Host button" in found      # light DOM of the host page
    assert "Frame button" in found     # inside the same-origin iframe
    assert "Frame link" in found


def test_iframe_elements_carry_frame_provenance(iframe_page):
    btn = next(e for e in iframe_page.elements
               if e.accessible_name == "Frame button")
    assert btn.frame == "same-origin"
    assert btn.frame_path  # host-page selector for the <iframe>
    # dom_path is relative to the frame, so it must not claim to be page-level.
    assert "iframe" not in btn.dom_path


def test_frames_are_recorded_on_the_page(iframe_page):
    keys = {f.key for f in iframe_page.frames}
    assert "same-origin" in keys
    same = next(f for f in iframe_page.frames if f.key == "same-origin")
    assert same.traversed is True
    assert same.same_origin is True
    assert same.element_count > 0


def test_iframe_element_counts_reported(iframe_page):
    assert iframe_page.counts.get("iframe_elements", 0) >= 3


def test_cross_origin_frame_is_recorded_but_not_entered(iframe_page):
    cross = next(f for f in iframe_page.frames if f.key == "cross-origin")
    assert cross.same_origin is False
    assert cross.traversed is False
    assert cross.element_count == 0
    assert "cross-origin" in (cross.reason or "")
    # Its contents must not have leaked into the page's elements.
    assert not [e for e in iframe_page.elements if e.frame == "cross-origin"]


def test_cross_origin_contents_are_absent_from_the_model(iframe_page):
    # The same child document is embedded twice — once same-origin, once
    # cross-origin. Exactly one copy of its controls should be present.
    frame_buttons = [e for e in iframe_page.elements
                     if e.accessible_name == "Frame button"]
    assert len(frame_buttons) == 1
    assert frame_buttons[0].frame == "same-origin"


def test_iframe_elements_are_never_clicked_by_the_probe(iframe_page):
    # A frame-relative dom_path resolved against the page could match a
    # different element entirely, so these must be observe-only.
    from ui_discovery.safety import decide

    for el in iframe_page.elements:
        if not el.frame:
            continue
        decision = decide(el.model_dump())
        assert decision.skipped_reason == "inside an iframe (observed only)"
