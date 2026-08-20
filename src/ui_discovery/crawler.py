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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import SCHEMA_VERSION, __version__
from . import adapters as adapter_hooks
from .adapters import Adapter
from .auth import check_auth
from .browser import (
    DOM_FINGERPRINT_JS,
    LIVE_CONNECTION_PROBE_JS,
    READ_LIVE_CONNECTIONS_JS,
    STREAMING_STABLE_POLLS,
    STREAMING_TIMEOUT_MS,
    has_rendered,
)
from .extraction import (
    JS,
    assemble_page,
    merge_frame_extraction,
    plan_frames,
    skipped_frame,
)
from .interactions import attach_network_async, probe_open_page_async
from .models import Crawl, CrawlConfig, CrawlStats, NetworkRequest, PageNode
from .safety import (
    DEFAULT_POLICY,
    SafetyPolicy,
    classify_label,
    decide,
    should_execute,
)
from .util import bfs_depths, normalize_url, resolve_links, slug_for, url_in_scope


async def _wait_for_dom_stable_async(
    page,
    *,
    networkidle: bool = True,
    live_connections: int = 0,
    timeout_ms: int = 8000,
    interval_ms: int = 250,
    required_stable_polls: int = 2,
) -> dict:
    """Async twin of `browser.wait_for_dom_stable` — see there for why an
    empty body never counts as stable."""
    if not networkidle:
        # Still fetching: require a full second of quiet, and allow longer.
        required_stable_polls = max(required_stable_polls, 4)
        timeout_ms = max(timeout_ms, 15000)
    # A held-open connection is re-checked every poll rather than decided
    # here: the socket usually opens a second or two into page load, so
    # sampling once up front races it and reads zero on a page that is about
    # to hold one open for the rest of its life.
    strict = False

    t0 = time.monotonic()
    deadline = t0 + timeout_ms / 1000
    last = None
    stable_polls = 0
    saw_content = False
    while time.monotonic() < deadline:
        try:
            fp = await page.evaluate(DOM_FINGERPRINT_JS)
        except Exception:
            break
        saw_content = saw_content or has_rendered(fp)
        if not strict:
            try:
                if int(await page.evaluate(READ_LIVE_CONNECTIONS_JS) or 0):
                    strict = True
                    required_stable_polls = max(
                        required_stable_polls, STREAMING_STABLE_POLLS)
                    deadline = max(deadline, t0 + STREAMING_TIMEOUT_MS / 1000)
            except Exception:
                pass
        if fp == last and has_rendered(fp):
            stable_polls += 1
            if stable_polls >= required_stable_polls:
                return {
                    "dom_stable": True,
                    "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
                    "held_open_connection": strict,
                }
        else:
            stable_polls = 0
        last = fp
        await page.wait_for_timeout(interval_ms)
    return {
        "dom_stable": False,
        "dom_stable_wait_ms": round((time.monotonic() - t0) * 1000),
        "dom_content_seen": saw_content,
        "held_open_connection": strict,
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
        signals.update(await _wait_for_dom_stable_async(
            page, networkidle=signals["networkidle"]))
    else:
        signals["dom_stable"] = False
        signals["dom_stable_wait_ms"] = 0
    return signals


async def _extract_frames_async(page, raw: dict) -> list:
    """Async twin of `extraction.extract_frames_sync` — same policy (enter
    same-origin frames only), same records."""
    records = []
    children = [f for f in page.frames if f is not page.main_frame]
    for plan in plan_frames(page.url, children, raw.get("frames", []) or []):
        if not plan["same_origin"]:
            records.append(skipped_frame(plan))
            continue
        try:
            frame_raw = await plan["frame"].evaluate(JS)
            records.append(merge_frame_extraction(raw, plan, frame_raw))
        except Exception as exc:
            plan["reason"] = f"frame could not be read: {str(exc).splitlines()[0][:120]}"
            records.append(skipped_frame(plan))
    return records



# Landmarks whose collapsed controls plausibly hide routes. Restricting the
# reveal pass to these is what keeps it safe and cheap: we are not clicking
# around the page, only opening the navigation.
_NAV_LANDMARKS = {"navigation", "banner", "complementary"}
_MAX_REVEALS = 12
_MAX_DEEP_CLICKS = 40  # per crawl, not per page


async def _reveal_nav_links(page, raw: dict, log) -> list[str]:
    """Expand collapsed navigation, then report any hrefs that appear.

    Routes hidden behind an accordion or an overflow menu are invisible to
    link-following, because their anchors are not in the DOM until something
    is clicked. This opens those controls — and only those: candidates must
    sit in a navigation landmark *and* pass the same safety gates as the
    probe, so a "Delete" button in a sidebar is still refused.

    Returns the newly-revealed hrefs. Used for discovery only; the captured
    page model still describes the page as it arrived.
    """
    before = {e.get("attributes", {}).get("href")
              for e in raw.get("elements", [])
              if e.get("attributes", {}).get("href")}

    candidates = []
    for el in raw.get("elements", []):
        if el.get("landmark") not in _NAV_LANDMARKS:
            continue
        attrs = el.get("attributes", {}) or {}
        if attrs.get("aria-expanded") != "false" and not attrs.get("aria-haspopup"):
            continue
        decision = decide(el)
        if should_execute(decision):
            candidates.append(decision)

    opened = 0
    for interaction in candidates[:_MAX_REVEALS]:
        try:
            handle = await page.query_selector(interaction.dom_path)
            if handle is None:
                continue
            await handle.click(timeout=2000)
            await page.wait_for_timeout(250)
            opened += 1
        except Exception:
            continue

    if not opened:
        return []

    try:
        after_raw = await page.evaluate(JS)
    except Exception:
        return []
    after = {e.get("attributes", {}).get("href")
             for e in after_raw.get("elements", [])
             if e.get("attributes", {}).get("href")}
    revealed = sorted(h for h in after - before if h)
    if revealed:
        log.info(f"Revealed {len(revealed)} link(s) behind {opened} nav control(s)")
    return revealed



# Elements the app treats as clickable but did not mark up as such: no anchor,
# no button, no ARIA role — just `cursor: pointer` and a handler. They are
# invisible to link-following *and* to the accessibility tree, which is an
# accessibility defect in the app, but they still hide real routes behind
# them. `cursor: pointer` is the one standards-based signal that survives:
# it is the app telling the browser "this is clickable".
CLICKABLE_CANDIDATES_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('*').forEach(el => {
    if (el.matches('a[href],button,input,select,textarea,[role=button],[role=link],[role=tab]')) return;
    if (el.closest('a[href],button,[role=button],[role=link]')) return;
    if (getComputedStyle(el).cursor !== 'pointer') return;
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    // A readable label is required, not cosmetic: it is what lets the safety
    // classifier refuse "Delete workspace". Unlabelled means unjudgeable,
    // so we leave it alone.
    if (!text || text.length > 40) return;
    if (seen.has(text)) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    // Prefer the outermost element still carrying only this label, so we
    // click the row rather than the text node inside it.
    let target = el;
    while (target.parentElement
           && getComputedStyle(target.parentElement).cursor === 'pointer'
           && (target.parentElement.innerText || '').replace(/\s+/g, ' ').trim() === text) {
      target = target.parentElement;
    }
    seen.add(text);
    out.push({text, path: cssPathFor(target)});
  });
  return out;

  function cssPathFor(el) {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 40) {
      let sel = el.nodeName.toLowerCase();
      if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
        parts.unshift(sel + '#' + CSS.escape(el.id));
        break;
      }
      let nth = 1, sib = el;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName === el.nodeName) nth++;
      }
      parts.unshift(sel + ':nth-of-type(' + nth + ')');
      el = el.parentElement;
    }
    return parts.join(' > ');
  }
}
"""


async def _count_unmarked_clickables(page) -> int:
    """How many clickable-but-unmarked elements this page has. Recorded even
    when deep discovery is off, so a capture can *say* it may be incomplete
    instead of just being incomplete."""
    try:
        return len(await page.evaluate(CLICKABLE_CANDIDATES_JS))
    except Exception:
        return 0


async def _discover_by_clicking(
    page, url: str, log, policy,
    tried: set[str], budget: list[int],
) -> list[str]:
    """Click unmarked clickables and record any route they navigate to.

    This is the last resort for navigation that link-following cannot see.
    Each candidate must carry a readable label, and that label goes through
    the same safety classifier as everything else — so "Delete workspace" is
    refused here exactly as it would be in the probe.

    After each click we return to `url`, so the crawl stays where it was.

    `tried` is shared across the whole crawl. Site-wide navigation is
    identical on every page, so without it the same sidebar gets clicked once
    per page — which on a 60-page crawl is thousands of pointless clicks and
    turns a two-minute capture into an hour.
    """
    try:
        candidates = await page.evaluate(CLICKABLE_CANDIDATES_JS)
    except Exception:
        return []

    async def hrefs_now() -> set[str]:
        try:
            return set(await page.evaluate(
                "() => [...document.querySelectorAll('a[href]')]"
                ".map(a => a.getAttribute('href')).filter(Boolean)"))
        except Exception:
            return set()

    baseline = await hrefs_now()
    found: list[str] = []
    attempted = 0
    for cand in candidates:
        if attempted >= _MAX_DEEP_CLICKS:
            break
        if budget[0] <= 0:
            break
        label = cand.get("text", "")
        if label in tried:
            continue
        tried.add(label)
        if classify_label(label, policy) != "SAFE":
            log.info(f"deep-nav: refusing {label!r} (not classified safe)")
            continue
        budget[0] -= 1
        try:
            handle = await page.query_selector(cand["path"])
            if handle is None:
                continue
            attempted += 1
            await handle.click(timeout=2000)
            await page.wait_for_timeout(600)

            # Two ways a click can reveal a route, and both count. Navigating
            # is the obvious one; the commoner one in a sidebar is expanding a
            # submenu, which puts new anchors in the DOM without moving.
            landed = page.url
            if normalize_url(landed) != normalize_url(url):
                found.append(landed)
                log.info(f"deep-nav: {label!r} navigated -> {landed}")
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(400)
                baseline = await hrefs_now()
                continue

            revealed = await hrefs_now() - baseline
            if revealed:
                found.extend(revealed)
                baseline |= revealed
                log.info(f"deep-nav: {label!r} revealed {len(revealed)} link(s)")
        except Exception:
            # A click that fails or lands somewhere unusable is not worth
            # failing the page over; get back and carry on.
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(300)
                baseline = await hrefs_now()
            except Exception:
                break
    return found


async def _aria(page) -> str | None:
    try:
        return await page.locator("body").aria_snapshot()
    except Exception:
        return None


def _concurrency_kwargs(
    max_requests_per_minute: float | None, max_concurrency: int | None,
) -> dict:
    """Crawlee options for politeness — or nothing at all.

    Returning `{}` when neither limit is set is the point: Crawlee gives
    browser crawlers `desired_concurrency=1` precisely because parallel
    browser pages starve each other's rendering, and passing our own
    ConcurrencySettings unconditionally overrode that with 10. On a real
    portal that made pages settle half-rendered — one dropped from 528
    elements to 28 — and two crawls of an *unchanged* site then diffed to
    594 phantom removals.

    So: only speak up when asked, and keep desired_concurrency at 1.
    """
    from crawlee import ConcurrencySettings

    if max_requests_per_minute is None and max_concurrency is None:
        return {}
    ceiling = max_concurrency if max_concurrency is not None else 100
    return {
        "concurrency_settings": ConcurrencySettings(
            max_concurrency=ceiling,
            desired_concurrency=1,  # match Crawlee's browser-crawler default
            max_tasks_per_minute=(max_requests_per_minute
                                  if max_requests_per_minute else float("inf")),
        )
    }


@dataclass(frozen=True)
class CrawlOptions:
    """Everything tunable about a crawl, in one object.

    These were twenty-odd keyword arguments on `crawl_site`, which had stopped
    being readable and made it impossible to pass a crawl's settings around as
    a value. Grouping them costs nothing at the call site — `crawl_site` still
    accepts them as keywords — but gives callers something they can build,
    inspect, and reuse.

    Every default reproduces the engine's behavior with no configuration.
    """

    # Budget
    max_pages: int = 25
    max_depth: int = 3
    max_interactions: int = 40
    # Runtime
    headless: bool = True
    # Page identity (H1)
    dedupe_queries: bool = False
    drop_params: frozenset[str] | None = None
    hash_routes: bool = False
    # Scope (S1)
    include: list[str] | None = None
    exclude: list[str] | None = None
    # Capabilities (R2)
    probe: bool = False
    screenshots: bool = True
    accessibility_tree: bool = True
    # Auth-expiry signals (H4) — these extend the built-ins, never replace them
    login_url_patterns: tuple[str, ...] | None = None
    logged_out_signals: tuple[str, ...] | None = None
    # Safety & privacy
    policy: SafetyPolicy = DEFAULT_POLICY
    redact_keys: tuple[str, ...] = ()
    # Extensibility (R3)
    adapters: tuple[Adapter, ...] = ()
    # Coverage
    #
    # `seeds` are extra start URLs crawled alongside `start_url`. Some routes
    # are simply unreachable by following links — a contextual sidebar that
    # only renders its own section, or a nav item that is a click handler
    # rather than an anchor. No amount of crawling finds those; you have to
    # be told they exist.
    seeds: tuple[str, ...] = ()
    # Expand collapsed navigation before reading links, so routes hidden
    # behind an accordion or menu are discovered. On by default: the clicks
    # are limited to navigation landmarks and pass the same safety gates as
    # the probe, so the worst case is an opened menu.
    reveal_nav: bool = True
    # Click elements the app never marked up as links — no anchor, no button,
    # no ARIA role, only a pointer cursor — and record where they lead.
    #
    # On by default. It was off while unproven; it is now the difference
    # between reaching 0 of 7 requested screens on a real portal and reaching
    # 7 of 7, the click budget is per-crawl rather than per-page, and every
    # label still passes the same safety classifier, so a "Delete workspace"
    # in a sidebar is refused here exactly as anywhere else. A capture that
    # silently omits whole sections is worse than one that takes a minute
    # longer.
    deep_nav: bool = True
    # Politeness (X5)
    max_requests_per_minute: float | None = None
    # None means 'leave Crawlee's own default alone'. That default is
    # desired_concurrency=1 for browser crawlers, chosen because parallel
    # browser pages starve each other's rendering — passing a value here
    # unconditionally silently raised it to 10 and made pages settle
    # half-rendered.
    max_concurrency: int | None = None
    respect_robots_txt: bool = False

    def replace(self, **overrides) -> "CrawlOptions":
        """A copy with `overrides` applied. An unknown name raises TypeError,
        so a typo is still an error rather than a silently ignored setting."""
        if "adapters" in overrides and overrides["adapters"] is not None:
            overrides["adapters"] = tuple(overrides["adapters"])
        return replace(self, **{k: v for k, v in overrides.items()
                                if v is not None or k in _NULLABLE})


# Fields whose `None` is meaningful rather than "not specified".
_NULLABLE = frozenset({
    "drop_params", "include", "exclude", "login_url_patterns",
    "logged_out_signals", "max_requests_per_minute", "max_concurrency",
})


async def crawl_site(
    start_url: str,
    *,
    output_dir: str = "output",
    auth_state: dict | None = None,
    options: CrawlOptions | None = None,
    **overrides,
) -> Crawl:
    """Crawl a same-domain site starting at `start_url`, extracting a UI model
    for every discovered page, and return an assembled `Crawl`.

    Settings come from `options` (a `CrawlOptions`), and any keyword arguments
    override individual fields — so `crawl_site(url, max_depth=2)` still works
    exactly as before, and an unknown keyword is still a TypeError.

    `dedupe_queries` / `drop_params` / `hash_routes` control page identity
    (see `util.normalize_url`) and are applied consistently to both the page
    graph we build and Crawlee's own request queue, so page counts match.

    With `probe=True`, every crawled page is additionally run through the V3
    safe-interaction + network probe (H2) — as the same logged-in user, on
    the page the crawler already has open. Only structurally-safe controls
    are executed; see `interactions.probe_open_page_async`."""
    opts = (options or CrawlOptions()).replace(**overrides)

    # Unpacked into locals so the body below reads the same as it always has.
    max_pages, max_depth = opts.max_pages, opts.max_depth
    max_interactions, headless = opts.max_interactions, opts.headless
    dedupe_queries, drop_params = opts.dedupe_queries, opts.drop_params
    hash_routes, include, exclude = opts.hash_routes, opts.include, opts.exclude
    probe, screenshots = opts.probe, opts.screenshots
    accessibility_tree = opts.accessibility_tree
    login_url_patterns = opts.login_url_patterns
    logged_out_signals = opts.logged_out_signals
    policy, redact_keys = opts.policy, opts.redact_keys
    adapters = opts.adapters
    seeds, reveal_nav, deep_nav = opts.seeds, opts.reveal_nav, opts.deep_nav
    max_requests_per_minute = opts.max_requests_per_minute
    max_concurrency = opts.max_concurrency
    respect_robots_txt = opts.respect_robots_txt
    # Imported here so the module imports cleanly even if crawlee isn't
    # installed (V0-only environments).
    from crawlee import Request, service_locator
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

    def _normalize(u: str) -> str:
        return normalize_url(
            u,
            dedupe_queries=dedupe_queries,
            drop_params=drop_params,
            hash_routes=hash_routes,
        )

    active_adapters = adapters or []

    def _in_scope(u: str) -> bool:
        # Scope rules first, then adapter vetoes (R3). An adapter can
        # narrow the crawl, never widen it past the config.
        if not url_in_scope(u, include, exclude):
            return False
        return adapter_hooks.should_visit(active_adapters, u)

    root = _normalize(start_url)
    shots_dir = Path(output_dir) / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    nodes: dict[str, PageNode] = {}
    edges: dict[str, list[str]] = {}
    # H2: one network sink per open page, keyed by page identity. Populated
    # from a pre-navigation hook so page-load traffic is captured, then read
    # by the handler once that page has been extracted.
    net_sinks: dict[int, list[NetworkRequest]] = {}
    # Per page: clickable elements the app never marked up as links.
    unmarked_total: dict[str, int] = {}
    # Shared across the crawl: labels already deep-clicked, the routes
    # they revealed, and a global click budget.
    deep_tried: set[str] = set()
    deep_found: set[str] = set()
    deep_budget = [_MAX_DEEP_CLICKS]

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
        respect_robots_txt_file=respect_robots_txt,
        **_concurrency_kwargs(max_requests_per_minute, max_concurrency),
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

    @crawler.pre_navigation_hook
    async def _install_connection_probe(context) -> None:  # noqa: ANN001
        try:
            await context.page.context.add_init_script(LIVE_CONNECTION_PROBE_JS)
        except Exception:
            pass

    # H2: start observing network traffic before the page navigates, so the
    # probe's record includes page-load requests, not just those triggered by
    # the interactions we execute.
    if probe:
        @crawler.pre_navigation_hook
        async def _attach_probe_network(context) -> None:  # noqa: ANN001
            sink: list[NetworkRequest] = []
            net_sinks[id(context.page)] = sink
            attach_network_async(context.page, sink, redact_keys)

    if active_adapters:
        @crawler.pre_navigation_hook
        async def _adapter_pre_nav(context) -> None:  # noqa: ANN001
            await adapter_hooks.pre_navigate(active_adapters, context)

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        page = context.page
        url = _normalize(context.request.url)
        context.log.info(f"Extracting {url}")

        readiness = await _readiness(page, context.response)
        # R3: adapter waits run after the generic readiness checks and
        # before anything is read, so they can cover what those miss.
        await adapter_hooks.post_navigate(active_adapters, page)
        raw = await page.evaluate(JS)
        frames = await _extract_frames_async(page, raw)
        aria = await _aria(page) if accessibility_tree else None

        shot: str | None = None
        if screenshots:
            shot = str(shots_dir / f"{slug_for(url)}.png")
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
            frames=frames,
        )

        # H4: a login page reached *with* a session in hand means that session
        # is no longer good. Warn per page — a silent crawl of login screens is
        # the failure mode this exists to prevent.
        model.auth = check_auth(
            model,
            login_url_patterns=login_url_patterns,
            logged_out_signals=logged_out_signals,
        )
        adapter_hooks.on_page(active_adapters, model)
        # R3: a product-specific signed-in check overrides the generic one.
        verdict = adapter_hooks.is_logged_in(active_adapters, model)
        if verdict is not None:
            model.auth.looks_logged_out = not verdict
            if not verdict and not model.auth.signal:
                model.auth.signal = "adapter"
                model.auth.evidence = "an adapter reported not-signed-in"
        if (model.auth.looks_logged_out or model.auth.looks_empty) and auth_state:
            context.log.warning(
                f"Session may be rejected at {url}: "
                f"{model.auth.signal} ({model.auth.evidence})"
            )

        # Build the page graph from the extracted anchors (deterministic, and
        # independent of Crawlee's internal enqueue bookkeeping).
        hrefs = [
            e.attributes.get("href", "")
            for e in model.elements
            if e.category in ("link", "button") and e.attributes.get("href")
        ]
        if reveal_nav:
            hrefs = hrefs + await _reveal_nav_links(page, raw, context.log)

        # Routes behind elements the app never marked up as links.
        unmarked = await _count_unmarked_clickables(page)
        if deep_nav:
            if unmarked:
                deep_found.update(await _discover_by_clicking(
                    page, url, context.log, policy, deep_tried, deep_budget))
            # Routes found anywhere are worth trying from here too: the same
            # global nav is on every page, and Crawlee dedups the rest.
            hrefs = hrefs + sorted(deep_found)
        unmarked_total[url] = unmarked

        out_links = resolve_links(
            model.final_url or url, hrefs, root,
            dedupe_queries=dedupe_queries,
            drop_params=drop_params,
            hash_routes=hash_routes,
        )
        edges[url] = out_links
        node = PageNode(url=url, out_links=out_links, page=model)
        nodes[url] = node

        # Enqueue the links *we* resolved, rather than letting Crawlee
        # re-scan the DOM. Two reasons: our list already carries everything
        # revealed by expanding navigation or by deep-nav clicks, and by this
        # point the page has been interacted with, so a fresh scan can see a
        # collapsed menu — or nothing at all. Passing the list explicitly
        # makes the queue and the page graph the same set by construction.
        #
        # These are already normalized and scope-filtered; Crawlee still owns
        # dedup, depth and the page budget.
        # Scope and adapter vetoes are applied here, at the queue. They used
        # to live in the enqueue transform, which passing an explicit request
        # list bypasses — an excluded area would otherwise be crawled anyway.
        # The page graph still records every link the page really has.
        queueable = [u for u in out_links if _in_scope(u)]
        if queueable:
            # Explicit unique_key, because Crawlee's default strips the
            # fragment — which would collapse every `#/route` of a
            # hash-routed SPA into a single request (H1).
            await context.enqueue_links(requests=[
                Request.from_url(u, unique_key=u) for u in queueable
            ])

        # H2: probe last — after extraction, the screenshot and link discovery
        # have all seen the pristine page. Interactions only ever mutate state
        # we've already captured. A probe failure must not fail the crawl: the
        # page's extraction is still valid without it.
        if probe:
            try:
                node.probe = await probe_open_page_async(
                    page,
                    url=url,
                    raw=raw,
                    network=net_sinks.pop(id(page), []),
                    max_interactions=max_interactions,
                    policy=policy,
                )
                p = node.probe.stats
                context.log.info(
                    f"Probed {url}: {p.get('executed', 0)} executed, "
                    f"{p.get('blocked', 0)} blocked, "
                    f"{p.get('network_requests', 0)} requests"
                )
            except Exception as exc:
                context.log.warning(f"Probe failed for {url}: {exc}")

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    # Seeds join the start URL; Crawlee dedups, and out-of-scope seeds are
    # dropped by the same transform as any other request.
    start_urls = [start_url] + [u for u in seeds if _normalize(u) != root]
    final_stats = await crawler.run(start_urls)
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
    logged_out = sum(
        1 for n in nodes.values() if n.page.auth and n.page.auth.looks_logged_out
    )
    empty = sum(
        1 for n in nodes.values() if n.page.auth and n.page.auth.looks_empty
    )

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
            dedupe_queries=dedupe_queries,
            hash_routes=hash_routes,
            probe=probe,
            auth_used=bool(auth_state),
            include=list(include or []),
            exclude=list(exclude or []),
            deep_nav=deep_nav,
            unmarked_clickables=sum(unmarked_total.values()),
            capabilities={
                "screenshots": screenshots,
                "accessibility_tree": accessibility_tree,
                "probe": probe,
            },
        ),
        stats=CrawlStats(
            pages_crawled=len(nodes),
            pages_failed=final_stats.requests_failed,
            unique_urls=len(discovered),
            links_discovered=len(navigation),
            runtime_seconds=round(runtime, 3),
            pages_logged_out=logged_out,
            pages_empty=empty,
            # Only an expiry if we actually presented a session. A blank app
            # counts: some SPAs render nothing rather than redirect when their
            # token is rejected.
            auth_expired=bool(auth_state) and (logged_out + empty) > 0,
        ),
        navigation=navigation,
        pages=ordered,
    )
