"""G6 — keep people out of the captured screenshots.

`G5` cleaned the model and left the harder half untouched. A capture of an
authenticated portal is mostly *pictures* of that portal, and a picture of a
customer list is a customer list. A `page.json` with no email in it, sitting
beside a `screenshots/` folder that shows every email, has protected nobody.

The assertions here read pixels rather than trusting the box arithmetic,
because every interesting failure of this feature is a mask painted in the
wrong place:

  * **The coordinate bug.** `extract.js` records `getBoundingClientRect` —
    *viewport* coordinates — while a full-page screenshot is in *document*
    coordinates. They agree only while the page has not scrolled, so a target
    below the fold is where a naive implementation puts the box somewhere else
    entirely. The fixture puts a seeded value 1400px down for exactly this.
  * **The crop translation.** A component crop has its own origin. A mask that
    is right in the full-page shot can still be wrong in the crop.
  * **Over-masking.** `Element.text` is `textContent`, so an email in one cell
    is also in the text of its row, its table, its `<main>` and its `<body>`.
    Masking every element that matched paints the page black and destroys the
    capture — which would pass any "is the secret gone?" check while being
    completely useless. Half the assertions below are about what must stay
    visible.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from ui_discovery.config import Privacy
from ui_discovery.extraction import JS, mask_targets_for_raw
from ui_discovery.mask import (
    LAYER_ID,
    apply_mask,
    clear_mask,
    paths_by_frame,
)
from ui_discovery.redact import DISABLED, MaskTarget, RedactionPolicy, Redactor

ON = RedactionPolicy(enabled=True)

BLACK = (0, 0, 0)


# --- a PNG reader, so the suite gains no dependency -------------------------
#
# Pillow is not a dependency of this project and G6 is not a reason to make it
# one: reading a handful of pixels out of a Chromium screenshot needs `zlib`
# and forty lines, and `test_no_ai_runtime`-style discipline about the
# dependency list is worth more than the convenience.


class Png:
    """Just enough PNG to answer "what colour is this pixel?"."""

    def __init__(self, data: bytes) -> None:
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        idat = bytearray()
        pos = 8
        while pos < len(data):
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            kind = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            if kind == b"IHDR":
                (self.width, self.height, depth, self.colour,
                 _comp, _filt, interlace) = struct.unpack(">IIBBBBB", body)
                assert depth == 8, f"unexpected bit depth {depth}"
                assert interlace == 0, "interlaced PNG not supported"
                assert self.colour in (2, 6), f"unexpected colour type {self.colour}"
            elif kind == b"IDAT":
                idat += body
            elif kind == b"IEND":
                break
            pos += 12 + length
        self.channels = 3 if self.colour == 2 else 4
        self._rows = self._unfilter(zlib.decompress(bytes(idat)))

    def _unfilter(self, raw: bytes) -> list[bytearray]:
        stride = self.width * self.channels
        rows: list[bytearray] = []
        prev = bytearray(stride)
        pos = 0
        for _ in range(self.height):
            filter_type = raw[pos]
            line = bytearray(raw[pos + 1:pos + 1 + stride])
            pos += 1 + stride
            bpp = self.channels
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up = prev[i]
                up_left = prev[i - bpp] if i >= bpp else 0
                if filter_type == 1:
                    line[i] = (line[i] + left) & 0xFF
                elif filter_type == 2:
                    line[i] = (line[i] + up) & 0xFF
                elif filter_type == 3:
                    line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
                elif filter_type == 4:
                    p = left + up - up_left
                    pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                    pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else up_left)
                    line[i] = (line[i] + pred) & 0xFF
            rows.append(line)
            prev = line
        return rows

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        x, y = int(x), int(y)
        assert 0 <= x < self.width and 0 <= y < self.height, \
            f"({x},{y}) outside {self.width}x{self.height}"
        off = x * self.channels
        row = self._rows[y]
        return (row[off], row[off + 1], row[off + 2])

    def fraction_black(self) -> float:
        total = self.width * self.height
        if not total:
            return 0.0
        dark = 0
        # Every 4th pixel in each direction: this is a statistic, not a proof,
        # and sampling keeps a 1400px-tall page from costing seconds.
        for y in range(0, self.height, 4):
            for x in range(0, self.width, 4):
                if self.pixel(x, y) == BLACK:
                    dark += 1
        return dark / max(1, (self.height // 4 + 1) * (self.width // 4 + 1))


def read_png(path) -> Png:
    return Png(Path(path).read_bytes())


def centre(box: dict) -> tuple[int, int]:
    return (box["x"] + box["width"] // 2, box["y"] + box["height"] // 2)


# --- pure units -------------------------------------------------------------


def test_a_disabled_policy_produces_no_targets():
    """The whole feature is inert unless redaction ran. There is nothing to
    mask that redaction did not find."""
    raw = {"elements": [_raw("p", "alice@acme.example", "main > p:nth-of-type(1)")]}
    assert mask_targets_for_raw(raw, DISABLED) == []


def test_targets_are_only_the_elements_that_matched():
    raw = {"elements": [
        _raw("p", "alice@acme.example", "main > p:nth-of-type(1)"),
        _raw("p", "Order 1234567890123456", "main > p:nth-of-type(2)"),
    ]}
    targets = mask_targets_for_raw(raw, ON)
    assert [t.dom_path for t in targets] == ["main > p:nth-of-type(1)"]


def test_a_malformed_element_costs_a_box_not_the_capture():
    """The probe calls this while photographing a state and never raises over
    a picture; one unreadable entry must not take the rest with it."""
    raw = {"elements": [
        {"nonsense": True},
        _raw("p", "alice@acme.example", "main > p:nth-of-type(1)"),
    ]}
    assert len(mask_targets_for_raw(raw, ON)) == 1


def test_targets_are_grouped_by_frame():
    """A path found inside a same-origin iframe is relative to that frame;
    running it against the main document would resolve nothing at all."""
    grouped = paths_by_frame([
        MaskTarget("a"), MaskTarget("b", "http://host/inner"),
        MaskTarget("a"), MaskTarget(""),
    ])
    assert grouped == {None: ["a"], "http://host/inner": ["b"]}


def test_the_witness_only_fires_when_something_was_replaced():
    r = Redactor(ON)
    before = r.total
    r.text("Save")
    r.witness("main > button:nth-of-type(1)", since=before)
    assert r.mask_targets == []

    before = r.total
    r.text("alice@acme.example")
    r.witness("main > p:nth-of-type(1)", since=before)
    assert [t.dom_path for t in r.mask_targets] == ["main > p:nth-of-type(1)"]


def test_screenshot_masking_follows_content_redaction_unless_told_otherwise():
    """Two independent switches is how a clean model ends up beside an
    unmasked screenshot folder, so the default is a pairing."""
    assert Privacy().mask_screenshots() is False
    assert Privacy(redact_content=True).mask_screenshots() is True
    assert Privacy(redact_content=True,
                   redact_screenshots=False).mask_screenshots() is False
    assert Privacy(redact_screenshots=True).mask_screenshots() is True


def _raw(tag: str, text: str, dom_path: str) -> dict:
    return {
        "tag": tag, "category": "text", "role": None, "accessible_name": None,
        "accessible_name_source": None, "text": text, "visible": True,
        "enabled": True, "bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10},
        "attributes": {}, "dom_path": dom_path, "sibling_ordinal": 0,
        "landmark": None, "shadow_depth": 0, "options": [], "option_count": 0,
        "states": [], "value": None, "described_by": None,
    }


# --- against a real browser -------------------------------------

# The engine captures controls and containers — a bare `<p>` is not an element
# at all — so the unit a mask hangs on is the `<form>`, `<table>` or `<dialog>`
# whose text carried the value. That is what `ROADMAP` §G6 asks for ("for each
# element whose text `G5` redacted, paint its box"), and it is blunter than it
# first sounds: a table with one address in it is covered whole. Erring toward
# covering more is the right direction for this feature, but it is a real cost
# and the assertions below pin both halves of it.


@pytest.fixture
def masked_page(serve):
    """Open the fixture and hand back the live page."""
    from playwright.sync_api import sync_playwright

    server = serve("fixtures/pii")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 800}).new_page()
            page.goto(server.url("masked.html"), wait_until="load")
            yield page
        finally:
            browser.close()


def _boxes_by_id(page, ids: list[str]) -> dict[str, dict]:
    """Where each element actually is, in document coordinates.

    Read from the browser rather than from the model on purpose: the model's
    `bounding_box` is viewport-relative, and a test that shared that assumption
    would agree with the bug instead of catching it.
    """
    return page.evaluate(
        """(ids) => Object.fromEntries(ids.map((id) => {
             const r = document.getElementById(id).getBoundingClientRect();
             return [id, {x: r.left + window.scrollX, y: r.top + window.scrollY,
                          width: r.width, height: r.height}];
           }))""", ids)


def _mask(page) -> dict:
    return apply_mask(page, mask_targets_for_raw(page.evaluate(JS), ON))


def test_the_mask_covers_what_was_redacted_and_nothing_else(masked_page, tmp_path):
    result = _mask(masked_page)
    assert result["unresolved"] == 0, "every dom_path must resolve"
    assert result["masked"] > 0

    shot = tmp_path / "full.png"
    masked_page.screenshot(path=str(shot), full_page=True)
    png = read_png(shot)

    where = _boxes_by_id(masked_page, [
        "contact-form", "order-form", "deep-contacts", "deep-orders"])

    # Covered: a container above the fold, and one 1400px below it. The second
    # is the coordinate bug — a viewport-relative box lands nowhere near it.
    assert png.pixel(*centre(where["contact-form"])) == BLACK
    assert png.pixel(*centre(where["deep-contacts"])) == BLACK

    # Untouched: the containers holding identifiers a capture exists to keep.
    # An engine that blacked these out would pass every secrets check and be
    # worthless.
    assert png.pixel(*centre(where["order-form"])) != BLACK
    assert png.pixel(*centre(where["deep-orders"])) != BLACK


def test_only_the_narrowest_container_is_masked(masked_page, tmp_path):
    """`textContent` means an address in a table is also in the text of the
    form around it. Masking both would swallow the controls beside the table,
    and on a real page the chain runs all the way up."""
    raw = masked_page.evaluate(JS)
    paths = {t.dom_path for t in mask_targets_for_raw(raw, ON)}
    assert {"form#nested-form", "table#nested-table"} <= paths,         "the fixture must offer an ancestor and a descendant to choose between"

    result = apply_mask(masked_page, mask_targets_for_raw(raw, ON))
    assert result["masked"] < result["candidates"], "nothing was pruned"

    shot = tmp_path / "narrow.png"
    masked_page.screenshot(path=str(shot), full_page=True)
    png = read_png(shot)

    where = _boxes_by_id(masked_page, ["nested-table", "nested-keep"])
    assert png.pixel(*centre(where["nested-table"])) == BLACK
    # The button lives in the form but outside the table. If the ancestor had
    # been masked too, it would be gone.
    assert png.pixel(*centre(where["nested-keep"])) != BLACK

    assert png.fraction_black() < 0.25, "the page was mostly blacked out"


def test_the_mask_survives_a_component_crop(masked_page, tmp_path):
    """A crop has its own origin. The overlay lives in the page, so the crop
    carries it — but only because it was painted into the page rather than
    composited onto the full-page file afterwards."""
    _mask(masked_page)

    covered = tmp_path / "covered.png"
    masked_page.locator("#deep-contacts").screenshot(path=str(covered))
    assert read_png(covered).fraction_black() > 0.9,         "a masked container should crop to an opaque box"

    kept = tmp_path / "kept.png"
    masked_page.locator("#deep-orders").screenshot(path=str(kept))
    assert read_png(kept).fraction_black() < 0.1,         "an unmasked container must crop to a readable picture"


def test_a_revealed_state_is_masked_from_its_own_dom(masked_page, tmp_path):
    """A dialog is `display:none` at page load, so its box is zero-sized and
    there is nothing to paint. Its mask only exists if it is recomputed after
    the click that opened it — which is why the probe masks per state rather
    than inheriting the page-load pass."""
    at_load = _mask(masked_page)
    boxes_at_load = at_load["boxes"]
    clear_mask(masked_page)

    masked_page.click("#open")
    masked_page.wait_for_timeout(100)
    after = _mask(masked_page)
    assert len(after["boxes"]) > len(boxes_at_load),         "opening the dialog must add a box"

    crop = tmp_path / "state.png"
    masked_page.locator("#settlement").screenshot(path=str(crop))
    assert read_png(crop).fraction_black() > 0.9

    # The control that opened it is not part of the state and stays readable.
    shot = tmp_path / "state-full.png"
    masked_page.screenshot(path=str(shot), full_page=True)
    where = _boxes_by_id(masked_page, ["open"])
    assert read_png(shot).pixel(*centre(where["open"])) != BLACK


def test_without_masking_the_page_is_captured_as_it_rendered(masked_page, tmp_path):
    """The control. A clean capture and an unmasked one must not look alike,
    or `screenshot_redaction` in the manifest means nothing."""
    shot = tmp_path / "plain.png"
    masked_page.screenshot(path=str(shot), full_page=True)
    png = read_png(shot)
    where = _boxes_by_id(masked_page, ["contact-form", "deep-contacts"])
    assert png.pixel(*centre(where["contact-form"])) != BLACK
    assert png.pixel(*centre(where["deep-contacts"])) != BLACK


def test_the_overlay_is_removed_so_a_later_extraction_cannot_read_it(masked_page):
    """The probe re-extracts after every interaction. A mask layer left in the
    DOM would be captured as page content — a redaction pass that *added*
    elements to the model would be a fine joke and a real bug."""
    _mask(masked_page)
    assert masked_page.evaluate(
        f"() => !!document.getElementById({LAYER_ID!r})") is True

    clear_mask(masked_page)
    assert masked_page.evaluate(
        f"() => !!document.getElementById({LAYER_ID!r})") is False

    after = masked_page.evaluate(JS)
    assert not [e for e in after["elements"] if LAYER_ID in (e.get("dom_path") or "")]


# --- end to end -------------------------------------------------------------


def test_a_crawl_writes_no_unmasked_picture(serve, tmp_path):
    """The wiring, not the mechanism: the crawler has to build the model
    *before* the shutter, because redaction is what discovers the boxes. An
    ordering regression here leaves every unit test above passing and every
    screenshot on disk unmasked.
    """
    import asyncio

    from ui_discovery.config import Privacy
    from ui_discovery.crawler import CrawlOptions, crawl_site
    from ui_discovery.redact import build_policy

    server = serve("fixtures/pii")
    privacy = Privacy(redact_content=True)
    crawl = asyncio.run(crawl_site(
        server.url("masked.html"),
        output_dir=str(tmp_path),
        options=CrawlOptions(
            max_pages=1, max_depth=0, probe=False,
            redaction=build_policy(privacy),
            mask_screenshots=privacy.mask_screenshots(),
        ),
    ))

    blob = json.dumps(crawl.model_dump(), ensure_ascii=False)
    for seeded in ("alice@acme.example", "grace@acme.example",
                   "4111 1111 1111 1111", "GB82 WEST 1234 5698 7654 32"):
        assert seeded not in blob, f"{seeded} survived into the model"
    for kept in ("1234567890123456", "12-345-678", "8080 9090"):
        assert kept in blob, f"{kept} was over-redacted out of the model"

    crops = {p.name: read_png(p) for p in tmp_path.rglob("*-*.png")}
    assert crops, "the crawl produced no component crops to check"
    covered = [p for p in crops.values() if p.fraction_black() > 0.9]
    readable = [p for p in crops.values() if p.fraction_black() < 0.1]
    assert covered, "no crop was masked — the containers carrying addresses"
    assert readable, "every crop was masked — the capture is worthless"

    full = [read_png(p) for p in tmp_path.rglob("*.png")
            if "-" not in p.stem.rsplit("_", 1)[-1]]
    for page_shot in full:
        assert page_shot.fraction_black() < 0.25, \
            "the full-page shot was mostly blacked out"
