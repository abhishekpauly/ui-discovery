"""Turn a live, rendered page into a validated `Page` model.

This module owns the browser session for a single URL: launch → navigate →
run the deterministic in-page pass (extract.js) → capture the ARIA snapshot and
a screenshot → assemble and validate the Pydantic model.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from . import SCHEMA_VERSION, __version__
from .auth import check_auth
from .browser import LIVE_CONNECTION_PROBE_JS, aria_snapshot, navigate
from .mask import apply_mask
from .models import Element, FrameInfo, Geometry, Heading, Option, Page
from .redact import RedactionPolicy, Redactor, redact_element, redact_heading
from .taxonomy import classify

# The deterministic in-page pass, shared by the sync extractor (V0) and the
# async Crawlee handler (V1). Public so the crawler can `page.evaluate(JS)`.
JS = (Path(__file__).with_name("extract.js")).read_text(encoding="utf-8")

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}


def _js_string_set(name: str) -> list[str]:
    """Read a `const NAME = new Set([...])` literal out of `extract.js`.

    G3 has to state which input types keep their value, and that list lives in
    the JS because that is where the decision is enforced. Parsing it back is
    unusual, and it is still the right call: mirroring the list in Python would
    create a second copy, and the copy that drifts is always the one nobody
    runs. Here there is exactly one source, and a rename raises rather than
    silently reporting an empty guarantee.
    """
    match = re.search(rf"const\s+{re.escape(name)}\s*=\s*new Set\(\[(.*?)\]\)",
                      JS, re.S)
    if not match:
        raise RuntimeError(
            f"{name} is no longer a `new Set([...])` literal in extract.js. "
            f"G3's data-handling posture reads it from there; update "
            f"`_js_string_set` rather than duplicating the list.")
    return sorted(re.findall(r'"([^"]+)"', match.group(1)))


def describe_redaction() -> dict:
    """G3: what the per-element pass refuses to keep.

    The types are read from `extract.js` rather than restated, so this cannot
    describe a guarantee the extractor does not actually make.
    """
    choice_types = _js_string_set("VALUE_SAFE_TYPES")
    return {
        "redactions": [
            {
                "rule": "element.typed_values",
                "applies_to": "Element.value on every input and textarea",
                "detail": (
                    "only choice-shaped values are recorded "
                    f"({', '.join(choice_types)}); free text, email, search, "
                    "tel, url and password values are dropped, keeping only "
                    "`has_value` — whether the field arrives pre-filled is a "
                    "fact about the UI, what it says is a fact about a person"),
            },
            {
                "rule": "element.value_attribute",
                "applies_to": "Element.attributes['value']",
                "detail": (
                    "kept only where it names a control or a choice; dropped "
                    "on every textarea and on any input whose type is not "
                    f"one of {', '.join(_js_string_set('VALUE_ATTR_SAFE_TYPES'))}"),
            },
        ],
        "value_recorded_for": choice_types,
    }


def _origin(url: str) -> tuple[str, str]:
    p = urlparse(url)
    return (p.scheme, p.netloc)


def same_origin(a: str, b: str) -> bool:
    """Same scheme+host+port. `about:blank` and `srcdoc` frames inherit their
    parent's origin, so they count as same-origin."""
    if a.startswith(("about:", "data:")):
        return True
    return _origin(a) == _origin(b)


def frame_key(frame, index: int) -> str:
    """A stable-ish identifier for a frame: its name/id if the page gave it
    one (Playwright's `frame.name` reflects the iframe's name or id), else a
    positional fallback."""
    return frame.name or f"frame[{index}]"


def plan_frames(page_url: str, frames: list, raw_frames: list[dict]) -> list[dict]:
    """Decide, for each child frame, whether to enter it — and record why not
    when we don't. Pure: takes the frame list and the in-page iframe inventory,
    returns one plan dict per frame. Shared by the sync and async extractors.

    `raw_frames` is `extract.js`'s iframe inventory, used only to recover the
    host-page selector for each frame (Playwright's `frame_element()` needs a
    round-trip and can fail on detached frames).
    """
    by_name = {f.get("name"): f for f in raw_frames if f.get("name")}
    plans = []
    for index, frame in enumerate(frames, start=1):
        key = frame_key(frame, index)
        inventory = by_name.get(frame.name) or {}
        is_same = same_origin(frame.url, page_url)
        plans.append({
            "frame": frame,
            "key": key,
            "url": frame.url,
            "dom_path": inventory.get("dom_path", ""),
            "title": inventory.get("title") or None,
            "same_origin": is_same,
            "reason": None if is_same else (
                "cross-origin frame — recorded but not traversed "
                "(third-party content is outside the product under test)"
            ),
        })
    return plans


def merge_frame_extraction(raw: dict, plan: dict, frame_raw: dict) -> FrameInfo:
    """Fold one traversed frame's elements/headings into the page's raw
    extraction, tagging each with its frame provenance, and return the
    FrameInfo record describing what was merged."""
    elements = frame_raw.get("elements", []) or []
    for el in elements:
        el["frame"] = plan["key"]
        el["frame_path"] = plan["dom_path"]
    raw.setdefault("elements", []).extend(elements)

    for heading in frame_raw.get("headings", []) or []:
        heading["frame"] = plan["key"]
        raw.setdefault("headings", []).append(heading)

    return FrameInfo(
        key=plan["key"],
        url=plan["url"],
        dom_path=plan["dom_path"],
        title=plan["title"],
        same_origin=True,
        traversed=True,
        element_count=len(elements),
    )


def skipped_frame(plan: dict) -> FrameInfo:
    return FrameInfo(
        key=plan["key"],
        url=plan["url"],
        dom_path=plan["dom_path"],
        title=plan["title"],
        same_origin=plan["same_origin"],
        traversed=False,
        reason=plan["reason"],
    )


def element_from_raw(raw: dict) -> Element:
    bb = raw.get("bounding_box")
    geometry = Geometry(**bb) if bb else None
    return Element(
        category=raw["category"],
        tag=raw["tag"],
        role=raw.get("role"),
        accessible_name=raw.get("accessible_name"),
        accessible_name_source=raw.get("accessible_name_source"),
        text=raw.get("text"),
        visible=bool(raw.get("visible", True)),
        enabled=bool(raw.get("enabled", True)),
        bounding_box=geometry,
        attributes=raw.get("attributes") or {},
        dom_path=raw.get("dom_path", ""),
        sibling_ordinal=int(raw.get("sibling_ordinal", 0)),
        landmark=raw.get("landmark"),
        shadow_depth=int(raw.get("shadow_depth", 0)),
        frame=raw.get("frame"),
        frame_path=raw.get("frame_path"),
        ui_type=classify(raw),
        # Relationship + state signals. Every one of these is optional in the
        # model, so a snapshot taken by an older engine still validates.
        options=[Option(**o) for o in (raw.get("options") or [])],
        option_count=int(raw.get("option_count", 0)),
        states={k: str(v) for k, v in (raw.get("states") or {}).items()},
        value=raw.get("value"),
        parent_path=raw.get("parent_path", "") or "",
        controls=list(raw.get("controls") or []),
        described_by=raw.get("described_by"),
        group=raw.get("group"),
        owner_form=raw.get("owner_form"),
        columns=list(raw.get("columns") or []),
        row_count=int(raw.get("row_count", 0)),
    )


def _counts(elements: list[Element], headings: list[Heading]) -> dict[str, int]:
    counts: dict[str, int] = {"headings": len(headings)}
    for el in elements:
        counts[el.category] = counts.get(el.category, 0) + 1
    counts["visible_elements"] = sum(1 for el in elements if el.visible)
    counts["total_elements"] = len(elements)
    # UI types are deliberately NOT folded into `counts`: that dict is
    # consumed by the crawl report and the per-screen inventory as the
    # category breakdown, and mixing a second taxonomy into it would corrupt
    # both. They are derived where they are used, from the elements.
    # H3: how much of the page was only reachable past a boundary.
    shadow = sum(1 for el in elements if el.shadow_depth)
    framed = sum(1 for el in elements if el.frame)
    if shadow:
        counts["shadow_dom_elements"] = shadow
    if framed:
        counts["iframe_elements"] = framed
    return counts


def assemble_page(
    *,
    requested_url: str,
    raw: dict,
    readiness: dict,
    aria_tree: Optional[str],
    screenshot_path: Optional[str],
    viewport: Optional[dict[str, int]] = None,
    frames: Optional[list[FrameInfo]] = None,
    redactor: Optional["Redactor"] = None,
) -> Page:
    """Pure model-builder: given the raw output of `JS` plus the readiness
    report / ARIA tree / screenshot path, assemble a validated `Page`.

    This is deliberately free of any browser object, so it can be shared by the
    sync extractor (V0) and the async Crawlee handler (V1) unchanged.

    G5: `redactor` runs here rather than at any of the write sites, and that is
    the whole design. This is the one point both capture paths pass through, so
    a single application covers the crawler and the extractor — and everything
    downstream (`elements.csv`, `controls.csv`, the reports, `docgen`) renders
    *from* this model, so redacting it once means no write site can leak a copy
    the others redacted. No unredacted `Page` is ever constructed.
    """
    elements = [element_from_raw(e) for e in raw.get("elements", [])]
    headings = [Heading(**h) for h in raw.get("headings", [])]
    title = raw.get("title", "")

    if redactor is not None and redactor.active:
        elements = [redact_element(e, redactor) for e in elements]
        for heading in headings:
            redact_heading(heading, redactor)
        title = redactor.text(title) or ""
        aria_tree = redactor.text(aria_tree)

    return Page(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        requested_url=requested_url,
        final_url=raw.get("final_url", requested_url),
        title=title,
        viewport=raw.get("viewport", viewport or DEFAULT_VIEWPORT),
        readiness=readiness,
        counts=_counts(elements, headings),
        headings=headings,
        elements=elements,
        frames=frames or [],
        accessibility_tree=aria_tree,
        screenshot_path=screenshot_path,
    )


def mask_targets_for_raw(raw: dict, policy: RedactionPolicy) -> list:
    """G6: which elements of a raw extraction a screenshot must cover.

    Used where there is no `Page` to read the answer off — the probe, which
    photographs a revealed state that exists only between two clicks and is
    never assembled into a page model.

    Deliberately runs the *same* `redact_element` over throwaway models rather
    than matching patterns against the raw dict directly. Detection has one
    implementation; a second one here would be the copy that drifts, and it
    would drift silently, because a mask that covers slightly less than the
    model redacted looks fine until someone reads the picture.
    """
    if not policy.enabled:
        return []
    redactor = Redactor(policy)
    for entry in raw.get("elements", []) or []:
        try:
            redact_element(element_from_raw(entry), redactor)
        except Exception:
            # A malformed entry costs one box, not the capture. The probe is
            # the caller and it never raises over a picture.
            continue
    return list(redactor.mask_targets)


def extract_frames_sync(page, raw: dict) -> list[FrameInfo]:
    """Traverse the page's same-origin child frames, merging their contents
    into `raw` and returning a record of every frame seen (entered or not).
    Never raises: a frame that navigates or detaches mid-extraction is
    recorded as not-traversed rather than failing the page."""
    records: list[FrameInfo] = []
    children = [f for f in page.frames if f is not page.main_frame]
    for plan in plan_frames(page.url, children, raw.get("frames", []) or []):
        if not plan["same_origin"]:
            records.append(skipped_frame(plan))
            continue
        try:
            frame_raw = plan["frame"].evaluate(JS)
            records.append(merge_frame_extraction(raw, plan, frame_raw))
        except Exception as exc:
            plan["reason"] = f"frame could not be read: {str(exc).splitlines()[0][:120]}"
            records.append(skipped_frame(plan))
    return records


def extract_page(
    url: str,
    *,
    screenshot_path: Optional[str] = None,
    viewport: Optional[dict[str, int]] = None,
    timeout_ms: int = 30000,
    headless: bool = True,
    auth_state: Optional[dict] = None,
    redaction: Optional[RedactionPolicy] = None,
    mask_screenshots: bool = False,
) -> Page:
    """Render `url` (sync Playwright) and return a validated `Page` model. If
    `screenshot_path` is given, a full-page screenshot is written there.
    `auth_state` is a Playwright storage-state dict for authenticated portals.

    G5: `redaction` strips people out of the captured text. Off unless asked
    for, so the default behaviour of this function is unchanged.

    G6: `mask_screenshots` covers those same elements in the image. It requires
    `redaction` — there is nothing to mask without it — and it is why the model
    is assembled before the shutter fires rather than after."""
    viewport = viewport or DEFAULT_VIEWPORT

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        try:
            context = browser.new_context(viewport=viewport, storage_state=auth_state)
            # Must be installed before any navigation: it wraps the WebSocket
            # and EventSource constructors so a held-open connection can be
            # detected, which is what selects the stricter settle profile.
            context.add_init_script(LIVE_CONNECTION_PROBE_JS)
            page = context.new_page()

            readiness = navigate(page, url, timeout_ms=timeout_ms)
            raw = page.evaluate(JS)
            frames = extract_frames_sync(page, raw)
            tree = aria_snapshot(page)

            # G6: the model comes first because redaction is what discovers
            # which elements carry a person — the mask has no input until it
            # has run.
            redactor = Redactor(redaction) if redaction else None
            page_model = assemble_page(
                requested_url=url,
                raw=raw,
                readiness=readiness,
                aria_tree=tree,
                screenshot_path=None,
                viewport=viewport,
                frames=frames,
                redactor=redactor,
            )

            if screenshot_path:
                Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
                if mask_screenshots and redactor and redactor.mask_targets:
                    apply_mask(page, redactor.mask_targets)
                page.screenshot(path=screenshot_path, full_page=True)
                page_model.screenshot_path = screenshot_path
            page_model.auth = check_auth(page_model)
            return page_model
        finally:
            browser.close()
