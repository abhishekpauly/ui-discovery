"""V3 interaction probe — discover what a page *does*, safely.

Loads one page, discovers interactive elements, and *executes only the
structurally-safe, reversible ones* (per safety.py) — recording a cheap
before/after state signature so we can tell what each interaction changed.
Meanwhile every network request is observed (method/url/status only, secrets
redacted). Nothing destructive is ever clicked.

Two entry points share one set of rules:

  * `probe_page(url, ...)` — sync Playwright, opens its own browser. This is
    the V3 CLI path (`python -m ui_discovery.probe`).
  * `probe_open_page_async(page, ...)` — takes an **already-open** async page
    and probes it in place. This is what the crawler calls (H2) so every
    crawled page can be probed as the logged-in user, without a second
    browser or a second login.

The safety decisions (`safety.decide` / `should_execute`) and the state
signature JS are shared by both, so "what is safe to click" has exactly one
definition. Only the await-vs-not plumbing is written twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import SCHEMA_VERSION, __version__
from .browser import navigate
from .extraction import JS
from .models import (
    Interaction,
    InteractionProbe,
    NetworkRequest,
    StateSignature,
    UIState,
)
from .network import classify as classify_request
from .network import redact_url
from .safety import ALLOW_LIST, DEFAULT_POLICY, SafetyPolicy, decide, should_execute
from .uistate import (
    classify_state,
    revealed_elements,
    state_filename,
    state_signature,
    visible_paths,
)

# --- probe profile ----------------------------------------------------------


@dataclass(frozen=True)
class ProbeProfile:
    """Fully-resolved probe settings for one area of a product.

    A single global "probe: on/off" is the wrong shape for a real portal. You
    want the Orders module exercised thoroughly, the Reports module read but
    never clicked, and — inside a module — only the tabs that matter opened,
    because opening the Audit Log tab on sixty screens is sixty pointless
    clicks and sixty pointless screenshots.

    Every field here is already resolved: no `Optional`, no inheritance left to
    do. `cliconfig` does the merging from the scope config; this is the probe's
    own contract, so using the library needs no `Scope`.
    """

    enabled: bool = True
    max_interactions: int = 40
    state_capture: bool = True
    component_screenshots: bool = True
    component_selectors: tuple[str, ...] = ()
    # all | none | listed
    tabs: str = "all"
    tab_labels: tuple[str, ...] = ()
    tab_exclude: tuple[str, ...] = ()

    def describe(self) -> dict:
        """The profile as plain data, for recording on a snapshot. A capture
        has to be able to say which tabs it chose not to open, or a reader
        cannot tell "this portal has no Audit tab" from "we skipped it"."""
        return {
            "enabled": self.enabled,
            "max_interactions": self.max_interactions,
            "state_capture": self.state_capture,
            "component_screenshots": self.component_screenshots,
            "component_selectors": list(self.component_selectors),
            "tabs": self.tabs,
            "tab_labels": list(self.tab_labels),
            "tab_exclude": list(self.tab_exclude),
        }


DEFAULT_PROBE_PROFILE = ProbeProfile()


def tab_allowed(label: str | None, profile: ProbeProfile) -> bool:
    """Whether the tab policy permits opening this tab.

    This can only ever *narrow* what gets clicked — exactly like `SafetyPolicy`,
    where config can make the engine more cautious and never less. There is
    deliberately no setting that widens the allow-list: a config file is the
    wrong place to be able to talk the engine into clicking something.

    Matching is case-insensitive and whitespace-collapsed, because a config is
    written by a person reading the screen, not copying the DOM.
    """
    name = " ".join((label or "").split()).lower()
    if any(name == " ".join(x.split()).lower() for x in profile.tab_exclude):
        return False
    if profile.tabs == "none":
        return False
    if profile.tabs == "listed":
        return any(name == " ".join(x.split()).lower()
                   for x in profile.tab_labels)
    return True


# A cheap, deterministic snapshot of page state used for before/after diffs.
_STATE_JS = """
() => {
  const vis = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return !(r.width === 0 && r.height === 0);
  };
  const dialogs = [...document.querySelectorAll('dialog,[role=dialog],[role=alertdialog]')]
      .filter(vis).length;
  const expanded = document.querySelectorAll('[aria-expanded="true"]').length;
  const inter = [...document.querySelectorAll(
      'a[href],button,input,select,textarea,[role=button],[role=tab],[role=menuitem],[role=link]')]
      .filter(vis).length;
  const t = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  // Cheap content hash so equal-length content swaps (e.g. tab panels) are
  // still detected as a change.
  let h = 0;
  for (let i = 0; i < t.length; i++) { h = ((h << 5) - h + t.charCodeAt(i)) | 0; }
  return { url: location.href, visible_interactive: inter,
           visible_dialogs: dialogs, expanded: expanded,
           text_len: t.length, content_hash: String(h) };
}
"""


def _state(page) -> StateSignature:
    return StateSignature(**page.evaluate(_STATE_JS))


def _duration_ms(request) -> float | None:
    """Round-trip time from Playwright's timing, or None if unavailable."""
    try:
        t = request.timing
        if t and t.get("responseEnd", -1) > 0 and t.get("requestStart", -1) >= 0:
            return round(t["responseEnd"] - t["requestStart"], 1)
    except Exception:
        pass
    return None


def _record(request, status: int | None,
            redact_keys: tuple[str, ...] = ()) -> NetworkRequest:
    """Build one observed-request record. Shared by the sync and async probes
    so both emit identical data — only the event plumbing differs. Every
    attribute read here is a plain property in both Playwright APIs."""
    method, url, rtype = request.method, request.url, request.resource_type
    is_api, is_gql, pattern = classify_request(method, url, rtype)
    return NetworkRequest(
        method=method,
        url=redact_url(url, redact_keys),
        resource_type=rtype,
        status=status,
        is_api=is_api,
        is_graphql=is_gql,
        endpoint_pattern=pattern,
        duration_ms=_duration_ms(request),
    )


def _attach_network(page, sink: list[NetworkRequest]) -> None:
    def on_finished(request):
        try:
            resp = request.response()
            sink.append(_record(request, resp.status if resp else None))
        except Exception:
            pass

    def on_failed(request):
        # Blocked / aborted / connection-refused requests never fire
        # "requestfinished"; record them too, with no status.
        try:
            sink.append(_record(request, None))
        except Exception:
            pass

    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_failed)


def attach_network_async(page, sink: list[NetworkRequest],
                         redact_keys: tuple[str, ...] = ()) -> None:
    """Async twin of `_attach_network`. Listens on "response" rather than
    "requestfinished" because the async `request.response()` is a coroutine —
    a `Response` hands us the status synchronously instead. Public so the
    crawler can attach it pre-navigation and catch page-load traffic."""
    def on_response(response):
        try:
            sink.append(_record(response.request, response.status, redact_keys))
        except Exception:
            pass

    def on_failed(request):
        try:
            sink.append(_record(request, None, redact_keys))
        except Exception:
            pass

    page.on("response", on_response)
    page.on("requestfailed", on_failed)


def _revert(page, before: StateSignature) -> bool:
    """Best-effort restore after an in-page toggle so later probes see a clean
    page. Returns True if state looks restored."""
    try:
        after = _state(page)
        if after.visible_dialogs > before.visible_dialogs or after.expanded != before.expanded:
            page.keyboard.press("Escape")
            page.wait_for_timeout(120)
        restored = _state(page)
        return (restored.url == before.url
                and restored.visible_dialogs == before.visible_dialogs)
    except Exception:
        return False


def _score(interaction: Interaction, before: StateSignature,
           after: StateSignature) -> None:
    """Fill in what an executed interaction changed. Pure comparison of two
    state signatures — shared by both probes so "what counts as a change" has
    one definition."""
    interaction.after = after
    interaction.executed = True
    interaction.route_changed = after.url != before.url
    interaction.dialog_opened = after.visible_dialogs > before.visible_dialogs
    interaction.expanded_changed = after.expanded != before.expanded
    interaction.dom_changed = (
        interaction.route_changed
        or interaction.dialog_opened
        or interaction.expanded_changed
        or after.visible_interactive != before.visible_interactive
        or after.content_hash != before.content_hash
    )



# --- what a click revealed --------------------------------------------------


def build_state(
    trigger: dict,
    before_visible: set[str],
    after_raw: dict,
    *,
    url: str,
) -> UIState | None:
    """Assemble a `UIState` from a pre-click baseline and a post-click
    extraction. Pure — the screenshot is attached by the caller, which is the
    only part that needs a browser.

    Returns None when the click changed something that is not a nameable
    state. That is not a failure: `Interaction.dom_changed` already records it,
    and a report full of pictures of nothing helps no one.
    """
    from .extraction import element_from_raw

    revealed = revealed_elements(before_visible, after_raw)
    found = classify_state(trigger, revealed, after=after_raw)
    if not found:
        return None

    container = found["dom_path"]
    # The controls this state reveals are the newly-visible elements inside
    # the container — not everything that appeared anywhere on the page.
    # A panel that reveals no controls of its own reveals none. Falling back to
    # "everything that changed anywhere" would credit the panel with the tab
    # that opened it, which is not in it.
    inside = [
        el for el in revealed
        if el.get("dom_path", "").startswith(container)
        and el.get("dom_path") != container
    ]

    controls = [element_from_raw(el) for el in inside[:60]]
    headings = [
        h.get("text", "") for h in after_raw.get("headings", [])
        if h.get("dom_path", "").startswith(container) and h.get("text")
    ]
    return UIState(
        kind=found["kind"],
        name=found["name"],
        trigger_label=(trigger.get("accessible_name") or trigger.get("text") or "").strip()[:120],
        trigger_path=trigger.get("dom_path", ""),
        page_url=url,
        dom_path=container,
        headings=headings[:10],
        controls=controls,
        fields=_fields_of(controls),
    )


def _fields_of(controls: list) -> list:
    """The input fields inside a revealed state, described as a form would be.

    A modal is usually a form. Reusing `relations` here means a dialog's fields
    are described exactly the same way a page's are, rather than by a second
    implementation that drifts.
    """
    from .relations import FIELD_CATEGORIES, describe_field

    return [describe_field(el) for el in controls if el.category in FIELD_CATEGORIES]


def _shot_path(states_dir, url: str, index: int, kind: str) -> str:
    from .util import slug_for

    return str(Path(states_dir) / state_filename(slug_for(url), index, kind))



def _signature_of(state) -> tuple:
    return state_signature(
        state.kind, state.trigger_label,
        [c.accessible_name or c.text or "" for c in state.controls],
    )


def _would_be_new(states: list, state) -> bool:
    """Whether this state is one we have not already captured."""
    signature = _signature_of(state)
    return all(_signature_of(existing) != signature for existing in states)


def _remember_state(states: list[UIState], state: UIState) -> bool:
    """Record a newly-captured state, or count it against one we already have.

    Returns True when the state is new and should be appended. A repeated
    component (a card grid, a table row) opens the same thing once per
    instance; the first capture keeps the screenshot and the rest just bump
    its count.
    """
    signature = _signature_of(state)
    for existing in states:
        if _signature_of(existing) == signature:
            existing.instances += 1
            return False
    return True


def _assemble_probe(
    *,
    url: str,
    final_url: str,
    title: str,
    interactions: list[Interaction],
    network: list[NetworkRequest],
    max_interactions: int,
    states: list[UIState] | None = None,
    profile=None,
) -> InteractionProbe:
    """Pure model-builder, shared by the sync and async probes — mirrors how
    `extraction.assemble_page` is shared by the extractor and the crawler."""
    stats = {
        "elements_seen": len(interactions),
        "executed": sum(1 for i in interactions if i.executed),
        "observed_only": sum(1 for i in interactions if not i.executed),
        "blocked": sum(1 for i in interactions if i.safety_label == "BLOCK"),
        "caution": sum(1 for i in interactions if i.safety_label == "CAUTION"),
        "state_changing": sum(1 for i in interactions if i.dom_changed),
        "network_requests": len(network),
        "api_requests": sum(1 for n in network if n.is_api),
        "states_captured": len(states or []),
    }
    return InteractionProbe(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        probed_at=datetime.now(timezone.utc).isoformat(),
        url=url,
        final_url=final_url,
        title=title,
        config={"max_interactions": max_interactions,
                "allow_list": sorted(ALLOW_LIST),
                "profile": (profile.describe() if profile is not None else None)},
        stats=stats,
        interactions=interactions,
        network=network,
        states=list(states or []),
    )


# --- async core (H2): probe a page the crawler already has open -------------

async def _state_async(page) -> StateSignature:
    return StateSignature(**await page.evaluate(_STATE_JS))


async def _revert_async(page, before: StateSignature) -> bool:
    """Async twin of `_revert`."""
    try:
        after = await _state_async(page)
        if after.visible_dialogs > before.visible_dialogs or after.expanded != before.expanded:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(120)
        restored = await _state_async(page)
        return (restored.url == before.url
                and restored.visible_dialogs == before.visible_dialogs)
    except Exception:
        return False


async def probe_open_page_async(
    page,
    *,
    url: str,
    raw: dict,
    network: list[NetworkRequest],
    max_interactions: int = 40,
    timeout_ms: int = 30000,
    policy: SafetyPolicy = DEFAULT_POLICY,
    states_dir: str | None = None,
    capture_states: bool = True,
    profile: ProbeProfile = DEFAULT_PROBE_PROFILE,
) -> InteractionProbe:
    """Probe an **already-open** async page in place, and return the result.

    Unlike `probe_page`, this opens no browser and performs no navigation —
    the caller (the crawler) has already navigated, as the logged-in user,
    and already run the extraction pass. `raw` is that extraction output, so
    we classify the elements it found rather than re-scanning the DOM;
    `network` is a sink the caller attached before navigation (see
    `attach_network_async`), so page-load traffic is captured too.

    The same safety rules apply as in the sync probe: only structurally-safe,
    reversible controls are executed, and anything that navigates is walked
    back so the crawler stays on course.

    With `capture_states`, each click that opens a modal, drawer, menu or tab
    panel also yields a `UIState`: the revealed controls, and — when
    `states_dir` is given — a screenshot of the thing that opened. This adds no
    interaction of its own; it photographs clicks the probe already performs.
    """
    interactions: list[Interaction] = []
    states: list[UIState] = []
    raw_by_path = {el.get("dom_path"): el for el in raw.get("elements", [])}
    # One baseline for the whole page: every executed interaction is reverted,
    # so the page returns here between clicks. A revert that fails only makes
    # the next diff conservative (fewer elements look new), never wrong.
    before_visible = visible_paths(raw) if capture_states else set()
    candidates = [decide(el, policy) for el in raw.get("elements", [])]

    executed_count = 0
    for interaction in candidates:
        if not should_execute(interaction):
            interactions.append(interaction)
            continue
        # Safety said yes; config says not this one. Recorded with a reason so
        # the report can show what was deliberately left unopened rather than
        # silently omitting it.
        if interaction.interaction_type == "tab" and not tab_allowed(
                interaction.target, profile):
            interaction.skipped_reason = "tab excluded by config"
            interactions.append(interaction)
            continue
        if executed_count >= max_interactions:
            interaction.skipped_reason = "interaction budget reached"
            interactions.append(interaction)
            continue

        try:
            handle = await page.query_selector(interaction.dom_path)
        except Exception:
            handle = None
        if handle is None:
            interaction.skipped_reason = "element not locatable"
            interactions.append(interaction)
            continue

        before = await _state_async(page)
        interaction.before = before
        try:
            await handle.click(timeout=3000)
            await page.wait_for_timeout(300)
            _score(interaction, before, await _state_async(page))
            executed_count += 1

            # Photograph what opened, before reverting closes it again.
            if capture_states and interaction.dom_changed and not interaction.route_changed:
                state = await _capture_state_async(
                    page,
                    trigger=raw_by_path.get(interaction.dom_path, {}),
                    before_visible=before_visible,
                    url=url,
                    index=len(states) + 1,
                    states_dir=states_dir,
                    seen=states,
                )
                if state is not None and _remember_state(states, state):
                    states.append(state)

            if interaction.route_changed:
                # An allow-listed type should not navigate; if it did, go back
                # so the rest of this page's probe — and the crawl itself —
                # stay on the page we were sent to extract.
                try:
                    await page.go_back(timeout=timeout_ms)
                    await page.wait_for_timeout(200)
                except Exception:
                    pass
                interaction.reverted = (await _state_async(page)).url == before.url
            else:
                interaction.reverted = await _revert_async(page, before)
        except Exception as exc:
            interaction.error = str(exc).splitlines()[0][:200]
        interactions.append(interaction)

    return _assemble_probe(
        url=url,
        final_url=raw.get("final_url", url),
        title=raw.get("title", ""),
        interactions=interactions,
        network=network,
        max_interactions=max_interactions,
        states=states,
        profile=profile,
    )



async def _capture_state_async(
    page, *, trigger: dict, before_visible: set[str], url: str,
    index: int, states_dir: str | None, seen: list | None = None,
):
    """Identify and photograph the state a click just opened.

    Never raises: a state we could not read or shoot is a missing picture, and
    losing the whole probe over one is a bad trade.
    """
    try:
        after_raw = await page.evaluate(JS)
    except Exception:
        return None
    state = build_state(trigger, before_visible, after_raw, url=url)
    if state is None or not states_dir:
        return state
    # A duplicate costs nothing beyond the classification above: no file, no
    # second picture of the same drawer.
    if seen is not None and not _would_be_new(seen, state):
        return state
    path = _shot_path(states_dir, url, index, state.kind)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Clipped to the revealed container, not the page: a modal photographed
        # full-page is a picture of the page behind it.
        await page.locator(state.dom_path).first.screenshot(path=path, timeout=5000)
        state.screenshot = path
    except Exception:
        pass
    return state


def _capture_state_sync(
    page, *, trigger: dict, before_visible: set[str], url: str,
    index: int, states_dir: str | None, seen: list | None = None,
):
    """Sync twin of `_capture_state_async`."""
    try:
        after_raw = page.evaluate(JS)
    except Exception:
        return None
    state = build_state(trigger, before_visible, after_raw, url=url)
    if state is None or not states_dir:
        return state
    if seen is not None and not _would_be_new(seen, state):
        return state
    path = _shot_path(states_dir, url, index, state.kind)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        page.locator(state.dom_path).first.screenshot(path=path, timeout=5000)
        state.screenshot = path
    except Exception:
        pass
    return state


def probe_page(
    url: str,
    *,
    max_interactions: int = 40,
    headless: bool = True,
    timeout_ms: int = 30000,
    auth_state: dict | None = None,
    policy: SafetyPolicy = DEFAULT_POLICY,
    states_dir: str | None = None,
    capture_states: bool = True,
    profile: ProbeProfile = DEFAULT_PROBE_PROFILE,
) -> InteractionProbe:
    network: list[NetworkRequest] = []
    interactions: list[Interaction] = []
    states: list[UIState] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        try:
            context = browser.new_context(storage_state=auth_state)
            page = context.new_page()
            _attach_network(page, network)

            navigate(page, url, timeout_ms=timeout_ms)
            page.wait_for_timeout(200)

            raw = page.evaluate(JS)
            title = raw.get("title", "")
            final_url = raw.get("final_url", url)

            # Classify every discovered element up front (pure, deterministic).
            raw_by_path = {el.get("dom_path"): el for el in raw.get("elements", [])}
            before_visible = visible_paths(raw) if capture_states else set()
            candidates = [decide(el, policy) for el in raw.get("elements", [])]

            executed_count = 0
            for interaction in candidates:
                if not should_execute(interaction):
                    interactions.append(interaction)
                    continue
                if interaction.interaction_type == "tab" and not tab_allowed(
                        interaction.target, profile):
                    interaction.skipped_reason = "tab excluded by config"
                    interactions.append(interaction)
                    continue
                if executed_count >= max_interactions:
                    interaction.skipped_reason = "interaction budget reached"
                    interactions.append(interaction)
                    continue

                handle = None
                try:
                    handle = page.query_selector(interaction.dom_path)
                except Exception:
                    handle = None
                if handle is None:
                    interaction.skipped_reason = "element not locatable"
                    interactions.append(interaction)
                    continue

                before = _state(page)
                interaction.before = before
                try:
                    handle.click(timeout=3000)
                    page.wait_for_timeout(300)
                    _score(interaction, before, _state(page))
                    executed_count += 1

                    if (capture_states and interaction.dom_changed
                            and not interaction.route_changed):
                        state = _capture_state_sync(
                            page,
                            trigger=raw_by_path.get(interaction.dom_path, {}),
                            before_visible=before_visible,
                            url=url,
                            index=len(states) + 1,
                            states_dir=states_dir,
                            seen=states,
                        )
                        if state is not None and _remember_state(states, state):
                            states.append(state)

                    if interaction.route_changed:
                        # An allow-listed type should not navigate; if it did,
                        # go back so remaining probes stay valid.
                        try:
                            page.go_back(timeout=timeout_ms)
                            page.wait_for_timeout(200)
                        except Exception:
                            pass
                        interaction.reverted = _state(page).url == before.url
                    else:
                        interaction.reverted = _revert(page, before)
                except Exception as exc:
                    interaction.error = str(exc).splitlines()[0][:200]
                interactions.append(interaction)

            return _assemble_probe(
                url=url,
                final_url=final_url,
                title=title,
                interactions=interactions,
                network=network,
                max_interactions=max_interactions,
                states=states,
                profile=profile,
            )
        finally:
            browser.close()
