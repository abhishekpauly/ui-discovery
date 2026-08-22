"""H9 — keep the furniture out of the model.

Cookie banners, chat widgets, session-timeout warnings and support bubbles are
on every screen of a real portal, and the engine dutifully models all of them.
They inflate element counts, invent components that span every page (`F2.3`
finds them and is technically right), and put a third-party vendor's UI in the
middle of a document about *your* product.

Two boundaries this must not blur:

  * **Excluding is not forbidding.** `safety.never_touch` stops the engine
    *interacting* with something the model still describes — the right answer
    for a Delete button. This stops it *modelling* at all, which is the right
    answer for a vendor's chat widget and the wrong answer for anything you
    would want in the document.
  * **A landmark is never excluded.** `main` and `nav` are the page's own
    structure. A selector broad enough to catch one is a mistake rather than an
    instruction, and refusing loudly beats quietly returning an empty capture.

And it is counted, not merely dropped: an exclusion nobody can see is
indistinguishable from a product that never had the thing.
"""

from __future__ import annotations

import pytest

from ui_discovery.config import Capture, Scope
from ui_discovery.extraction import extract_page

BANNER_WORDS = ("Accept all", "Reject non-essential", "We value your privacy")
CHAT_WORDS = ("Start chat", "Type a message")
PRODUCT_WORDS = ("Search orders", "Reference", "Orders")


def _texts(page) -> str:
    """Everything the model would let a reader see, as one blob."""
    parts = [page.title or ""]
    for element in page.elements:
        parts += [element.text or "", element.accessible_name or "",
                  *(element.attributes or {}).values(),
                  *(element.columns or [])]
    parts += [h.text for h in page.headings]
    return " | ".join(p for p in parts if p)


@pytest.fixture
def furniture(serve):
    return serve("fixtures/furniture")


def test_without_exclusion_the_furniture_is_modelled(furniture):
    """The control. If this ever stops being true the rest proves nothing."""
    page = extract_page(furniture.url("index.html"))
    blob = _texts(page)
    for word in BANNER_WORDS + CHAT_WORDS:
        assert word in blob, word


def test_an_excluded_subtree_contributes_no_elements(furniture):
    page = extract_page(furniture.url("index.html"),
                        exclude_selectors=("#cookie-banner", ".chat-widget"))
    blob = _texts(page)
    for word in BANNER_WORDS + CHAT_WORDS:
        assert word not in blob, f"{word} survived exclusion"


def test_the_product_itself_is_untouched(furniture):
    """Half of this feature is not removing the wrong thing."""
    page = extract_page(furniture.url("index.html"),
                        exclude_selectors=("#cookie-banner", ".chat-widget"))
    blob = _texts(page)
    for word in PRODUCT_WORDS:
        assert word in blob, f"{word} was removed with the furniture"


def test_exclusion_is_counted_rather_than_silent(furniture):
    plain = extract_page(furniture.url("index.html"))
    trimmed = extract_page(furniture.url("index.html"),
                           exclude_selectors=("#cookie-banner", ".chat-widget"))

    assert trimmed.excluded["count"] > 0
    assert trimmed.excluded["subtrees"] == 2
    # The count is real: it matches what actually left the model.
    dropped = (plain.counts["total_elements"] - trimmed.counts["total_elements"]
               + plain.counts["headings"] - trimmed.counts["headings"])
    assert trimmed.excluded["count"] == dropped

    by_selector = {s["selector"]: s["matched"]
                   for s in trimmed.excluded["selectors"]}
    assert by_selector == {"#cookie-banner": 1, ".chat-widget": 1}


def test_nothing_is_recorded_when_nothing_is_excluded(furniture):
    """The default has to stay quiet — every existing capture gains an empty
    section, not a noisy one."""
    page = extract_page(furniture.url("index.html"))
    assert page.excluded["count"] == 0
    assert page.excluded["subtrees"] == 0
    assert page.excluded["refused"] == []


def test_a_selector_matching_a_landmark_is_refused_with_a_reason(furniture):
    page = extract_page(furniture.url("index.html"), exclude_selectors=("main",))

    assert page.excluded["subtrees"] == 0, "the landmark must not be excluded"
    refused = page.excluded["refused"]
    assert len(refused) == 1
    assert refused[0]["selector"] == "main"
    assert "landmark" in refused[0]["reason"]

    # And the product is still there, which is the point of refusing.
    assert "Search orders" in _texts(page)


def test_a_refused_selector_does_not_block_the_others(furniture):
    """One bad selector in a list must not take the good ones with it."""
    page = extract_page(furniture.url("index.html"),
                        exclude_selectors=("main", "#cookie-banner"))
    blob = _texts(page)
    assert "Accept all" not in blob      # the good selector still applied
    assert "Search orders" in blob       # the refused one still refused
    assert len(page.excluded["refused"]) == 1


def test_a_selector_matching_nothing_is_reported_as_matching_nothing(furniture):
    """A typo'd selector should be visible in the record rather than looking
    like a widget that was not on the page."""
    page = extract_page(furniture.url("index.html"),
                        exclude_selectors=("#does-not-exist",))
    assert page.excluded["selectors"] == [
        {"selector": "#does-not-exist", "matched": 0}]
    assert page.excluded["count"] == 0


def test_an_invalid_selector_does_not_fail_the_capture(furniture):
    """`querySelectorAll` throws on malformed CSS. Losing a whole page to a
    stray bracket in a config would be a poor trade."""
    page = extract_page(furniture.url("index.html"),
                        exclude_selectors=("###nonsense[", "#cookie-banner"))
    assert "Accept all" not in _texts(page)
    assert "Search orders" in _texts(page)


# --- the config seam --------------------------------------------------------


def test_the_config_carries_the_selectors():
    scope = Scope(start_url="http://x/",
                  capture={"exclude_selectors": ["#cookie-banner"]})
    assert scope.capture.exclude_selectors == ["#cookie-banner"]


def test_capture_is_separate_from_safety():
    """They answer different questions and must not be conflated: one forbids
    modelling, the other forbids interacting."""
    assert "exclude_selectors" in Capture.model_fields
    assert "exclude_selectors" not in Scope.model_fields["safety"].annotation.model_fields
    assert Scope(start_url="http://x/").capture.exclude_selectors == []
