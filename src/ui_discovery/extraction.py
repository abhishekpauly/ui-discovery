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

from . import SCHEMA_VERSION, __version__
from .browser import aria_snapshot, navigate
from .models import Element, Geometry, Heading, Page

# The deterministic in-page pass, shared by the sync extractor (V0) and the
# async Crawlee handler (V1). Public so the crawler can `page.evaluate(JS)`.
JS = (Path(__file__).with_name("extract.js")).read_text(encoding="utf-8")

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}


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
    )


def _counts(elements: list[Element], headings: list[Heading]) -> dict[str, int]:
    counts: dict[str, int] = {"headings": len(headings)}
    for el in elements:
        counts[el.category] = counts.get(el.category, 0) + 1
    counts["visible_elements"] = sum(1 for el in elements if el.visible)
    counts["total_elements"] = len(elements)
    return counts


def assemble_page(
    *,
    requested_url: str,
    raw: dict,
    readiness: dict,
    aria_tree: Optional[str],
    screenshot_path: Optional[str],
    viewport: Optional[dict[str, int]] = None,
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
        accessibility_tree=aria_tree,
        screenshot_path=screenshot_path,
    )


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
            page = context.new_page()

            readiness = navigate(page, url, timeout_ms=timeout_ms)
            raw = page.evaluate(JS)
            tree = aria_snapshot(page)

            saved_screenshot: Optional[str] = None
            if screenshot_path:
                Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshot_path, full_page=True)
                saved_screenshot = screenshot_path

            return assemble_page(
                requested_url=url,
                raw=raw,
                readiness=readiness,
                aria_tree=tree,
                screenshot_path=saved_screenshot,
                viewport=viewport,
            )
        finally:
            browser.close()
