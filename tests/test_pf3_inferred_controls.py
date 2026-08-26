"""PF3 — model controls the app never marked up as controls.

The Model Catalog screen of a real portal grouped its models under
per-provider accordions. All three were in the captured accessibility tree:

    - paragraph: Anthropic
    - paragraph: 7 models · Anthropic, GCP Model Garden
    - paragraph: Google

None was among the screen's 47 modelled elements, and none was ever
interacted with — the headers are <p> elements with click handlers, and the
extractor models controls by role. The capture documented that screen's chrome
and none of its content, and said nothing about the gap.

Event listeners cannot be read from script, so the affordance the page offers a
POINTER is the honest signal: `cursor: pointer` is what an app uses to tell a
person "this is clickable". An element found that way is recorded as
`inferred`, never merged with a declared control, because the difference is
itself a finding — a control only a mouse can reach is one a screen reader
cannot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_discovery.extraction import extract_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


@pytest.fixture(scope="module")
def portaled_page():
    return extract_page(fixture_url("interactive/portaled.html"))


def test_a_bare_accordion_is_modelled_at_all(portaled_page):
    """The regression. `<p id="bare-accordion">OpenAI</p>` — no role, no
    button, no aria-expanded — used to be invisible to the element model."""
    inferred = [e for e in portaled_page.elements if e.inferred]
    assert inferred, "a pointer-affordant <p> was not modelled"
    assert any("OpenAI" in (e.text or e.accessible_name or "")
               for e in inferred), [e.text for e in inferred]


def test_an_inferred_control_says_it_is_inferred(portaled_page):
    """Never merged with a declared control. A <button> and a guess are
    different facts and the model must keep them different."""
    bare = [e for e in portaled_page.elements if e.inferred][0]
    assert bare.tag == "p"
    declared = [e for e in portaled_page.elements
                if not e.inferred and e.tag == "button"]
    assert declared, "the real buttons stopped being captured"
    assert all(e.inferred is False for e in declared)


def test_the_declared_accordion_is_not_reclassified(portaled_page):
    """The properly marked-up accordion beside it is still a declared button
    with `aria-expanded`. Inference must not swallow the thing it imitates."""
    named = [e for e in portaled_page.elements
             if (e.accessible_name or "").strip() == "Anthropic"]
    assert named, [e.accessible_name for e in portaled_page.elements]
    assert named[0].inferred is False
    assert named[0].tag == "button"


def test_a_layout_wrapper_is_not_a_control(portaled_page):
    """A div that merely contains buttons is a layout div. Capturing wrappers
    would bury the real controls in scaffolding."""
    for el in portaled_page.elements:
        if not el.inferred:
            continue
        assert el.tag not in ("div", "section", "main", "body"), (
            f"captured a wrapper as a control: {el.dom_path}")


def test_inference_can_be_turned_off(portaled_page):
    """`capture.infer_controls: false` — for a capture that must contain only
    what the product actually declared."""
    strict = extract_page(fixture_url("interactive/portaled.html"),
                          infer_controls=False)
    assert not [e for e in strict.elements if e.inferred]
    assert len(strict.elements) < len(portaled_page.elements)
