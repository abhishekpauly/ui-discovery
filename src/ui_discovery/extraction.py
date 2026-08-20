"""Turn a live, rendered page into a validated `Page` model.

This module owns the browser session for a single URL: launch → navigate →
run the deterministic in-page pass (extract.js) → capture the ARIA snapshot and
a screenshot → assemble and validate the Pydantic model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from urllib.parse import urlparse

from . import SCHEMA_VERSION, __version__
from .auth import check_auth
from .browser import LIVE_CONNECTION_PROBE_JS, aria_snapshot, navigate
from .models import Element, FrameInfo, Geometry, Heading, Page
from .taxonomy import classify

# The deterministic in-page pass, shared by the sync extractor (V0) and the
# async Crawlee handler (V1). Public so the crawler can `page.evaluate(JS)`.
JS = (Path(__file__).with_name("extract.js")).read_text(encoding="utf-8")

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}


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


def _element_from_raw(raw: dict) -> Element:
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
) -> Page:
    """Pure model-builder: given the raw output of `JS` plus the readiness
    report / ARIA tree / screenshot path, assemble a validated `Page`.

    This is deliberately free of any browser object, so it can be shared by the
    sync extractor (V0) and the async Crawlee handler (V1) unchanged.
    """
    elements = [_element_from_raw(e) for e in raw.get("elements", [])]
    headings = [Heading(**h) for h in raw.get("headings", [])]
    return Page(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        requested_url=requested_url,
        final_url=raw.get("final_url", requested_url),
        title=raw.get("title", ""),
        viewport=raw.get("viewport", viewport or DEFAULT_VIEWPORT),
        readiness=readiness,
        counts=_counts(elements, headings),
        headings=headings,
        elements=elements,
        frames=frames or [],
        accessibility_tree=aria_tree,
        screenshot_path=screenshot_path,
    )


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
) -> Page:
    """Render `url` (sync Playwright) and return a validated `Page` model. If
    `screenshot_path` is given, a full-page screenshot is written there.
    `auth_state` is a Playwright storage-state dict for authenticated portals."""
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

            saved_screenshot: Optional[str] = None
            if screenshot_path:
                Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshot_path, full_page=True)
                saved_screenshot = screenshot_path

            page_model = assemble_page(
                requested_url=url,
                raw=raw,
                readiness=readiness,
                aria_tree=tree,
                screenshot_path=saved_screenshot,
                viewport=viewport,
                frames=frames,
            )
            page_model.auth = check_auth(page_model)
            return page_model
        finally:
            browser.close()
