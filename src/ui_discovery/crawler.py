"""V1 crawler — Crawlee (PlaywrightCrawler) driving the V0 extractor.

Crawlee owns the *infrastructure* (request queue, dedup, retries, concurrency,
depth + page limits, same-domain restriction). Our code owns the *product*:
each discovered page is fed through the shared extractor, and the results are
assembled into a page graph. There is no framework-specific logic here.
"""

from __future__ import annotations

import json
import pathlib
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
    redact_aria_snapshot,
)
from .extraction import (
    JS,
    assemble_page,
    merge_frame_extraction,
    plan_frames,
    skipped_frame,
)
from .interactions import (
    ProbeProfile,
    attach_network_async,
    probe_open_page_async,
)
from .models import Crawl, CrawlConfig, CrawlStats, NetworkRequest, PageNode
from .safety import (
    DEFAULT_POLICY,
    SafetyPolicy,
    classify_label,
    decide,
    should_execute,
)
from .uistate import component_filename, component_targets
from .util import (
    bfs_depths,
    module_for_path,
    normalize_url,
    path_of,
    resolve_labelled_links,
    slug_for,
    url_in_scope,
)


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



def _link_record(el: dict, control: str = "link") -> dict:
    """One navigable link, with the label a person would click.

    The page graph used to be built from bare hrefs, which made every edge
    anonymous: a reader could see that Customers reaches Customer-1 but not
    that you get there by clicking "Ada Lovelace" in the main table. The label,
    its region and the kind of control are what make the graph readable.
    """
    attrs = el.get("attributes", {}) or {}
    label = (el.get("accessible_name") or el.get("text") or "").strip()
    return {
        "href": attrs.get("href", ""),
        "label": label[:120],
        "region": el.get("landmark") or "",
        "control": control,
    }


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

    Returns the newly-revealed links as `_link_record` dicts. Used for
    discovery only; the captured page model still describes the page as it
    arrived.
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
    revealed = []
    for el in after_raw.get("elements", []):
        href = (el.get("attributes", {}) or {}).get("href")
        if not href or href in before:
            continue
        before.add(href)
        revealed.append(_link_record(el))
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
) -> list[dict]:
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
                # The label of the thing we clicked is the only description
                # this route will ever have — nothing links to it.
                found.append({"href": landed, "label": label,
                              "region": "", "control": "deep-nav"})
                log.info(f"deep-nav: {label!r} navigated -> {landed}")
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(400)
                baseline = await hrefs_now()
                continue

            revealed = await hrefs_now() - baseline
            if revealed:
                found.extend({"href": h, "label": label, "region": "",
                              "control": "deep-nav"} for h in sorted(revealed))
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



async def _capture_components(page, raw: dict, shots_dir, url: str,
                              extra_selectors, log) -> dict[str, str]:
    """Photograph each component on the page, cropped to just that component.

    A full-page screenshot answers "what does this screen look like?". It does
    not answer "what is in the New Order form?" on a screen with four forms and
    a table, which is the question someone writing product documentation has.

    Returns `dom_path -> file`, folded onto the elements by the caller. Never
    raises: a crop that fails is a missing picture, not a failed page.
    """
    targets = component_targets(raw)

    # Site-specific components the standards cannot name — a card, a tile, a
    # dashboard widget. `taxonomy.NOT_DETECTABLE` records that these have no
    # standard markup; a CSS selector in the scope config is where that
    # knowledge belongs, rather than guessed-at class names in the core.
    for selector in extra_selectors or ():
        try:
            handles = await page.query_selector_all(selector)
        except Exception:
            continue
        for index, handle in enumerate(handles[:10], start=1):
            targets.append({
                "kind": "component",
                "dom_path": "",
                "name": f"{selector} #{index}",
                "handle": handle,
            })

    shot_paths: dict[str, str] = {}
    components_dir = pathlib.Path(shots_dir) / "components"
    for index, target in enumerate(targets, start=1):
        path = components_dir / component_filename(
            slug_for(url), index, target["kind"])
        try:
            components_dir.mkdir(parents=True, exist_ok=True)
            handle = target.get("handle")
            if handle is not None:
                await handle.screenshot(path=str(path), timeout=5000)
            else:
                await page.locator(target["dom_path"]).first.screenshot(
                    path=str(path), timeout=5000)
        except Exception:
            continue
        if target["dom_path"]:
            shot_paths[target["dom_path"]] = str(path)
    if shot_paths:
        log.info(f"Captured {len(shot_paths)} component screenshot(s)")
    return shot_paths


async def _aria(page) -> str | None:
    """Async twin of `browser.aria_snapshot` — same redaction, so a crawled
    page never carries typed field values the single-page extractor strips."""
    try:
        return redact_aria_snapshot(await page.locator("body").aria_snapshot())
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
    # Off for the *library*, on for the *product*. `crawl_site(url)` is the
    # low-level API: a programmatic caller should have to ask before the engine
    # starts clicking things. The CLIs and scope configs default it on
    # (`Capabilities.probe`), because a capture that never clicks anything
    # cannot see a modal, a menu, a tab panel or an API call.
    #
    # Same split, same reason, as `headless` below — see `cliconfig`.
    probe: bool = False
    screenshots: bool = True
    accessibility_tree: bool = True
    # Cropped screenshots of the components already on a page — forms, open
    # dialogs, visible tab panels, data tables, labelled regions. Costs one
    # extra shot per component and clicks nothing.
    component_screenshots: bool = True
    # CSS for components standard markup cannot name (cards, tiles, widgets).
    component_selectors: tuple[str, ...] = ()
    # Photograph the modal / drawer / menu / tab panel each probed click
    # reveals. Requires `probe`, since it rides on clicks the probe makes.
    state_capture: bool = True
    # How thoroughly to probe, per area of the product. `probe_default` applies
    # to any page no rule matches; `probe_rules` is (url-path prefix, profile),
    # longest prefix winning — the same matching that decides which module
    # folder a page's artifacts go to, so the two can never disagree.
    #
    # The plain `probe` / `max_interactions` fields above still work and seed
    # the default profile, so `crawl_site(url, probe=True)` is unchanged.
    probe_default: ProbeProfile | None = None
    probe_rules: tuple[tuple[str, ProbeProfile], ...] = ()
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
    run=None,
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
    are executed; see `interactions.probe_open_page_async`.

    `run` is an optional `run.RunContext`. When given, the crawl reports what
    it did page by page — captured, skipped for budget, probed, refused — into
    that run's event stream. The crawl is unchanged without one: a `crawl`
    invoked directly is still a complete artifact, it just leaves no trail."""
    opts = (options or CrawlOptions()).replace(**overrides)

    # Unpacked into locals so the body below reads the same as it always has.
    max_pages, max_depth = opts.max_pages, opts.max_depth
    max_interactions, headless = opts.max_interactions, opts.headless
    dedupe_queries, drop_params = opts.dedupe_queries, opts.drop_params
    hash_routes, include, exclude = opts.hash_routes, opts.include, opts.exclude
    probe, screenshots = opts.probe, opts.screenshots
    accessibility_tree = opts.accessibility_tree
    component_screenshots = opts.component_screenshots
    component_selectors = opts.component_selectors
    state_capture = opts.state_capture
    # A caller that passed only the plain flags still gets a coherent profile.
    probe_default = opts.probe_default or ProbeProfile(
        enabled=probe,
        max_interactions=max_interactions,
        state_capture=state_capture,
        component_screenshots=component_screenshots,
        component_selectors=tuple(component_selectors),
    )
    probe_rules = opts.probe_rules

    def profile_for(page_url: str) -> ProbeProfile:
        return module_for_path(path_of(page_url), list(probe_rules),
                               default=probe_default)
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

    def event(name: str, /, **data) -> None:
        """Report to the run, when there is one. A crawl without a run is not
        a degraded crawl, so this stays a no-op rather than a branch at every
        call site."""
        if run is not None:
            try:
                run.emit(name, stage="crawl", **data)
            except Exception:
                pass

    root = _normalize(start_url)
    shots_dir = Path(output_dir) / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    nodes: dict[str, PageNode] = {}
    # Pages whose budget slot is taken. Distinct from `nodes`, which is only
    # populated once a page has been fully extracted — see the handler.
    claimed: set[str] = set()
    edges: dict[str, list[str]] = {}
    # H2: one network sink per open page, keyed by page identity. Populated
    # from a pre-navigation hook so page-load traffic is captured, then read
    # by the handler once that page has been extracted.
    net_sinks: dict[int, list[NetworkRequest]] = {}
    # Per page: clickable elements the app never marked up as links.
    unmarked_total: dict[str, int] = {}
    # O4: where the crawl's time went. A dict rather than a `nonlocal` int for
    # the same reason as the line above — the page handler is a closure, and a
    # mutable container keeps the accumulation visible at the call site.
    timing: dict[str, int] = {"probe_ms": 0}
    # Shared across the crawl: labels already deep-clicked, the routes
    # they revealed, and a global click budget.
    deep_tried: set[str] = set()
    deep_found: dict[str, dict] = {}
    deep_budget = [_MAX_DEEP_CLICKS]
    # Per page: its outgoing links with the label of the control that reaches
    # each one. `edges` stays a plain url->urls map so BFS is unchanged.
    edge_labels: dict[str, list[dict]] = {}

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
    if probe or any(rule.enabled for _, rule in probe_rules):
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

        # Our own page budget, enforced here rather than left to Crawlee.
        #
        # Crawlee's `max_requests_per_crawl` counts *completed* requests and is
        # checked before dispatching the next one, so anything in flight — or
        # being retried — does not count yet. On a slow SPA that retried 29
        # requests, a budget of 25 produced 38 captured pages. The handler is
        # the only place that knows how many pages we have actually captured,
        # so it is the only place that can hold the line exactly.
        # The slot is *claimed* here, not when the node is finally recorded.
        # Everything between this point and `nodes[url] = node` awaits, so two
        # handlers would otherwise both pass a `len(nodes) < max_pages` check
        # and both add. Claiming with no await in between makes it atomic
        # under asyncio, and the budget exact rather than approximate.
        if url not in claimed:
            if len(claimed) >= max_pages:
                context.log.info(
                    f"Page budget ({max_pages}) reached; not capturing {url}")
                event("page.skipped", url=url, reason="page budget reached",
                      max_pages=max_pages)
                return
            claimed.add(url)

        context.log.info(f"Extracting {url}")

        readiness = await _readiness(page, context.response)
        # R3: adapter waits run after the generic readiness checks and
        # before anything is read, so they can cover what those miss.
        await adapter_hooks.post_navigate(active_adapters, page)
        raw = await page.evaluate(JS)
        frames = await _extract_frames_async(page, raw)
        aria = await _aria(page) if accessibility_tree else None

        profile = profile_for(url)

        shot: str | None = None
        component_shots: dict[str, str] = {}
        if screenshots:
            shot = str(shots_dir / f"{slug_for(url)}.png")
            try:
                await page.screenshot(path=shot, full_page=True)
            except Exception:
                shot = None
            if profile.component_screenshots:
                component_shots = await _capture_components(
                    page, raw, shots_dir, url, profile.component_selectors,
                    context.log)

        model = assemble_page(
            requested_url=url,
            raw=raw,
            readiness=readiness,
            aria_tree=aria,
            screenshot_path=shot,
            frames=frames,
        )

        for element in model.elements:
            if element.dom_path in component_shots:
                element.clip_screenshot = component_shots[element.dom_path]

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
            event("auth.rejected", level="warning", url=url,
                  signal=model.auth.signal, evidence=model.auth.evidence,
                  looks_logged_out=model.auth.looks_logged_out,
                  looks_empty=model.auth.looks_empty)

        # Build the page graph from the extracted anchors (deterministic, and
        # independent of Crawlee's internal enqueue bookkeeping). Each entry
        # carries the label of the control that leads there, so the graph can
        # say *how* one screen reaches another.
        links = [
            _link_record(e, control=e.get("category", "link"))
            for e in raw.get("elements", [])
            if e.get("category") in ("link", "button")
            and (e.get("attributes", {}) or {}).get("href")
        ]
        if reveal_nav:
            links = links + await _reveal_nav_links(page, raw, context.log)

        # Routes behind elements the app never marked up as links.
        unmarked = await _count_unmarked_clickables(page)
        if deep_nav:
            if unmarked:
                for record in await _discover_by_clicking(
                        page, url, context.log, policy, deep_tried, deep_budget):
                    deep_found[record["href"]] = record
            # Routes found anywhere are worth trying from here too: the same
            # global nav is on every page, and Crawlee dedups the rest.
            links = links + [deep_found[h] for h in sorted(deep_found)]
        unmarked_total[url] = unmarked

        labelled = resolve_labelled_links(
            model.final_url or url, links, root,
            dedupe_queries=dedupe_queries,
            drop_params=drop_params,
            hash_routes=hash_routes,
        )
        out_links = [link["url"] for link in labelled]
        edges[url] = out_links
        edge_labels[url] = labelled
        node = PageNode(url=url, out_links=out_links, page=model)
        nodes[url] = node
        event("page.captured", url=url, title=model.title,
              elements=model.counts.get("total_elements", 0),
              out_links=len(out_links),
              http_status=readiness.get("http_status"),
              components_cropped=len(component_shots))

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
        if profile.enabled:
            probe_t0 = time.monotonic()
            try:
                node.probe = await probe_open_page_async(
                    page,
                    url=url,
                    raw=raw,
                    network=net_sinks.pop(id(page), []),
                    max_interactions=profile.max_interactions,
                    policy=policy,
                    states_dir=str(shots_dir / "states") if screenshots else None,
                    capture_states=profile.state_capture,
                    profile=profile,
                )
                p = node.probe.stats
                context.log.info(
                    f"Probed {url}: {p.get('executed', 0)} executed, "
                    f"{p.get('blocked', 0)} blocked, "
                    f"{p.get('states_captured', 0)} states captured, "
                    f"{p.get('network_requests', 0)} requests"
                )
                event("probe.executed", url=url, executed=p.get("executed", 0),
                      states_captured=p.get("states_captured", 0),
                      api_requests=p.get("api_requests", 0))
                # Every refusal, with the reason. "We did not click Delete" is
                # a claim a capture should be able to substantiate.
                for interaction in node.probe.interactions:
                    if interaction.safety_label in ("BLOCK", "CAUTION"):
                        event("probe.refused", url=url,
                              target=interaction.target,
                              interaction_type=interaction.interaction_type,
                              verdict=interaction.safety_label,
                              reason=interaction.skipped_reason)
                for state in node.probe.states:
                    event("state.captured", url=url, kind=state.kind,
                          state_name=state.name, trigger=state.trigger_label,
                          instances=state.instances)
            except Exception as exc:
                context.log.warning(f"Probe failed for {url}: {exc}")
            finally:
                # O4: counted even when the probe failed. A probe that spent
                # nine seconds before falling over still spent them, and a
                # metric that quietly omits the slow cases is worse than none.
                timing["probe_ms"] += int((time.monotonic() - probe_t0) * 1000)

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

    # `from` and `to` stay exactly where they were — the extra keys are
    # additive, so anything already reading this list is unaffected.
    navigation = [
        {"from": src, "to": link["url"], "label": link["label"],
         "region": link["region"], "control": link["control"]}
        for src, links in edge_labels.items() for link in links
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

    missed = discovered - set(nodes)
    if missed:
        event("budget.exhausted", level="warning",
              discovered_not_captured=len(missed), max_pages=max_pages,
              examples=sorted(missed)[:10])

    return Crawl(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        crawl_id=uuid.uuid4().hex[:12],
        run_id=getattr(run, "run_id", None),
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
                "component_screenshots": component_screenshots,
                "state_capture": state_capture,
            },
            # A reader has to be able to tell "this portal has no Audit tab"
            # from "we chose not to open it".
            probe_profiles=(
                [{"scope": "(default)", **probe_default.describe()}]
                + [{"scope": prefix, **rule.describe()}
                   for prefix, rule in probe_rules]
            ),
        ),
        stats=CrawlStats(
            pages_crawled=len(nodes),
            pages_failed=final_stats.requests_failed,
            unique_urls=len(discovered),
            links_discovered=len(navigation),
            runtime_seconds=round(runtime, 3),
            pages_logged_out=logged_out,
            pages_empty=empty,
            # Only an expiry if we actually presented a session, AND the
            # evidence is proportionate to the claim.
            #
            # A login page reached while holding a session is unambiguous, so
            # one is enough. A *blank* page is not: an SPA renders nothing for
            # plenty of reasons that have nothing to do with auth — a deep link
            # missing the query params it needs, a route that only resolves
            # from inside the app. On a real portal three such pages out of
            # thirty-eight flagged the whole capture as "the login/blank state,
            # not the product" while thirty-five screens held real content.
            # Telling someone to throw away a good capture is as bad as missing
            # a bad one, so a blank-page verdict now requires blankness to be
            # the dominant outcome rather than merely present.
            auth_expired=bool(auth_state) and (
                logged_out > 0 or (empty > 0 and empty * 2 >= len(nodes))
            ),
            probe_ms=timing["probe_ms"],
        ),
        navigation=navigation,
        pages=ordered,
    )
