"""V1 crawler — Crawlee (PlaywrightCrawler) driving the V0 extractor.

Crawlee owns the *infrastructure* (request queue, dedup, retries, concurrency,
depth + page limits, same-domain restriction). Our code owns the *product*:
each discovered page is fed through the shared extractor, and the results are
assembled into a page graph. There is no framework-specific logic here.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import SCHEMA_VERSION, __version__
from .browser import DOM_FINGERPRINT_JS
from .extraction import JS, assemble_page
from .models import Crawl, CrawlConfig, CrawlStats, PageNode
from .util import bfs_depths, normalize_url, resolve_links, slug_for


async def _wait_for_dom_stable_async(
    page,
    *,
    timeout_ms: int = 4000,
    interval_ms: int = 250,
    required_stable_polls: int = 2,
) -> dict:
    """Async twin of `browser.wait_for_dom_stable` — see there for why this
    exists: `networkidle` fires before SPAs finish client-side rendering."""
    t0 = time.monotonic()
    deadline = t0 + timeout_ms / 1000
    last = None
    stable_polls = 0
    while time.monotonic() < deadline:
        try:
            fp = await page.evaluate(DOM_FINGERPRINT_JS)
        except Exception:
            break
        if fp == last:
            stable_polls += 1
            if stable_polls >= required_stable_polls:
                return {
                    "dom_stable": True,
                    "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
                }
        else:
            stable_polls = 0
        last = fp
        await page.wait_for_timeout(interval_ms)
    return {
        "dom_stable": False,
        "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
    }


async def _readiness(page, response) -> dict:
    signals: dict = {"http_status": response.status if response else None}
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        signals["networkidle"] = True
    except PlaywrightTimeoutError:
        signals["networkidle"] = False
    try:
        await page.wait_for_selector("body", state="attached", timeout=3000)
        signals["body_present"] = True
    except PlaywrightTimeoutError:
        signals["body_present"] = False

    # Past networkidle, keep polling until the DOM stops mutating — protects
    # extraction/screenshots from firing mid-render (see browser.py).
    if signals["body_present"]:
        signals.update(await _wait_for_dom_stable_async(page))
    else:
        signals["dom_stable"] = False
        signals["dom_stable_wait_ms"] = 0
    return signals


async def _aria(page) -> str | None:
    try:
        return await page.locator("body").aria_snapshot()
    except Exception:
        return None


async def crawl_site(
    start_url: str,
    *,
    max_pages: int = 25,
    max_depth: int = 3,
    output_dir: str = "output",
    headless: bool = True,
    auth_state: dict | None = None,
) -> Crawl:
    """Crawl a same-domain site starting at `start_url`, extracting a UI model
    for every discovered page, and return an assembled `Crawl`."""
    # Imported here so the module imports cleanly even if crawlee isn't
    # installed (V0-only environments).
    from crawlee import service_locator
    from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
    from crawlee.storage_clients import MemoryStorageClient

    # Crawlee caches storage instances (e.g. the default request queue) on a
    # global service locator. Clear it so a second crawl in the same process
    # starts from a clean queue rather than an already-drained one.
    try:
        service_locator.storage_instance_manager.clear_cache()
    except Exception:
        pass

    # Self-contained crawls: pin Crawlee's tldextract to its bundled
    # public-suffix snapshot so same-domain checks make NO network fetch. This
    # keeps the engine dependent on nothing beyond the target site. Best-effort
    # — a safe no-op if Crawlee internals change.
    try:
        import crawlee._utils.urls as _cu
        from tldextract import TLDExtract

        _offline = TLDExtract(suffix_list_urls=(), cache_dir=tempfile.mkdtemp())
        _cu._get_tld_extractor = lambda: _offline
    except Exception:
        pass

    root = normalize_url(start_url)
    shots_dir = Path(output_dir) / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    nodes: dict[str, PageNode] = {}
    edges: dict[str, list[str]] = {}

    crawler = PlaywrightCrawler(
        headless=headless,
        browser_type="chromium",
        # --no-sandbox is required when the process runs as root (e.g. CI /
        # containers). Harmless elsewhere.
        browser_launch_options={"args": ["--no-sandbox"]},
        # A fresh in-memory store per crawl: no cross-run state, no on-disk
        # bookkeeping left in the project tree.
        storage_client=MemoryStorageClient(),
        max_requests_per_crawl=max_pages,   # page budget
        max_crawl_depth=max_depth,          # depth budget
        max_request_retries=2,              # retry budget
    )

    # Authenticated portals: apply the saved session (cookies + localStorage)
    # to each browser context before it navigates. Crawlee's browser pool does
    # not honor storage_state via context options, so we inject via a hook.
    if auth_state:
        cookies = auth_state.get("cookies") or []
        origins = auth_state.get("origins") or []
        _init_done: set[int] = set()

        @crawler.pre_navigation_hook
        async def _apply_auth(context) -> None:  # noqa: ANN001
            pctx = context.page.context
            if cookies:
                try:
                    await pctx.add_cookies(cookies)
                except Exception:
                    pass
            key = id(pctx)
            if origins and key not in _init_done:
                _init_done.add(key)
                for origin in origins:
                    items = origin.get("localStorage") or []
                    if not items:
                        continue
                    kv = {i["name"]: i["value"] for i in items}
                    js = (
                        "(() => { if (location.origin === "
                        + json.dumps(origin.get("origin", ""))
                        + ") { const kv = " + json.dumps(kv)
                        + "; for (const k in kv) localStorage.setItem(k, kv[k]); } })();"
                    )
                    try:
                        await pctx.add_init_script(js)
                    except Exception:
                        pass

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        page = context.page
        url = normalize_url(context.request.url)
        context.log.info(f"Extracting {url}")

        readiness = await _readiness(page, context.response)
        raw = await page.evaluate(JS)
        aria = await _aria(page)

        shot: str | None = str(shots_dir / f"{slug_for(url)}.png")
        try:
            await page.screenshot(path=shot, full_page=True)
        except Exception:
            shot = None

        model = assemble_page(
            requested_url=url,
            raw=raw,
            readiness=readiness,
            aria_tree=aria,
            screenshot_path=shot,
        )

        # Build the page graph from the extracted anchors (deterministic, and
        # independent of Crawlee's internal enqueue bookkeeping).
        hrefs = [
            e.attributes.get("href", "")
            for e in model.elements
            if e.category in ("link", "button") and e.attributes.get("href")
        ]
        out_links = resolve_links(model.final_url or url, hrefs, root)
        edges[url] = out_links
        nodes[url] = PageNode(url=url, out_links=out_links, page=model)

        # Let Crawlee discover + enqueue same-domain links (it handles dedup,
        # depth and the page budget).
        await context.enqueue_links(strategy="same-domain")

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    final_stats = await crawler.run([start_url])
    runtime = time.monotonic() - t0
    finished = datetime.now(timezone.utc)

    # Depth via BFS over the discovered edges (start URL = 0).
    depths = bfs_depths(root, edges)
    for url, node in nodes.items():
        node.depth = depths.get(url)

    navigation = [
        {"from": src, "to": dst} for src, outs in edges.items() for dst in outs
    ]
    ordered = sorted(
        nodes.values(),
        key=lambda n: (n.depth if n.depth is not None else 10**9, n.url),
    )
    discovered = set(nodes) | {dst for outs in edges.values() for dst in outs}

    return Crawl(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        crawl_id=uuid.uuid4().hex[:12],
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        config=CrawlConfig(
            start_url=start_url,
            max_pages=max_pages,
            max_depth=max_depth,
            strategy="same-domain",
        ),
        stats=CrawlStats(
            pages_crawled=len(nodes),
            pages_failed=final_stats.requests_failed,
            unique_urls=len(discovered),
            links_discovered=len(navigation),
            runtime_seconds=round(runtime, 3),
        ),
        navigation=navigation,
        pages=ordered,
    )
