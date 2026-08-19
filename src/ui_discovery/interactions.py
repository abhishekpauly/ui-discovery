"""V3 interaction probe (single page, sync Playwright).

Loads one page, discovers interactive elements, and *executes only the
structurally-safe, reversible ones* (per safety.py) — recording a cheap
before/after state signature so we can tell what each interaction changed.
Meanwhile every network request is observed (method/url/status only, secrets
redacted). Nothing destructive is ever clicked.
"""

from __future__ import annotations

from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from . import SCHEMA_VERSION, __version__
from .browser import navigate
from .extraction import JS
from .models import (
    Interaction,
    InteractionProbe,
    NetworkRequest,
    StateSignature,
)
from .network import classify as classify_request
from .network import redact_url
from .safety import ALLOW_LIST, decide, should_execute

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


def _attach_network(page, sink: list[NetworkRequest]) -> None:
    def on_finished(request):
        try:
            resp = request.response()
            status = resp.status if resp else None
            rtype = request.resource_type
            method = request.method
            url = request.url
            is_api, is_gql, pattern = classify_request(method, url, rtype)
            duration = None
            try:
                t = request.timing
                if t and t.get("responseEnd", -1) > 0 and t.get("requestStart", -1) >= 0:
                    duration = round(t["responseEnd"] - t["requestStart"], 1)
            except Exception:
                duration = None
            sink.append(NetworkRequest(
                method=method,
                url=redact_url(url),
                resource_type=rtype,
                status=status,
                is_api=is_api,
                is_graphql=is_gql,
                endpoint_pattern=pattern,
                duration_ms=duration,
            ))
        except Exception:
            pass

    def on_failed(request):
        # Blocked / aborted / connection-refused requests never fire
        # "requestfinished"; record them too, with no status.
        try:
            rtype = request.resource_type
            method = request.method
            url = request.url
            is_api, is_gql, pattern = classify_request(method, url, rtype)
            sink.append(NetworkRequest(
                method=method,
                url=redact_url(url),
                resource_type=rtype,
                status=None,
                is_api=is_api,
                is_graphql=is_gql,
                endpoint_pattern=pattern,
                duration_ms=None,
            ))
        except Exception:
            pass

    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_failed)


def _revert(page, interaction: Interaction, before: StateSignature) -> bool:
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


def probe_page(
    url: str,
    *,
    max_interactions: int = 40,
    headless: bool = True,
    timeout_ms: int = 30000,
    auth_state: dict | None = None,
) -> InteractionProbe:
    network: list[NetworkRequest] = []
    interactions: list[Interaction] = []

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
            candidates = [decide(el) for el in raw.get("elements", [])]

            executed_count = 0
            for interaction in candidates:
                if not should_execute(interaction):
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
                    after = _state(page)
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
                    executed_count += 1

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
                        interaction.reverted = _revert(page, interaction, before)
                except Exception as exc:
                    interaction.error = str(exc).splitlines()[0][:200]
                interactions.append(interaction)

            stats = {
                "elements_seen": len(candidates),
                "executed": sum(1 for i in interactions if i.executed),
                "observed_only": sum(1 for i in interactions if not i.executed),
                "blocked": sum(1 for i in interactions if i.safety_label == "BLOCK"),
                "caution": sum(1 for i in interactions if i.safety_label == "CAUTION"),
                "state_changing": sum(1 for i in interactions if i.dom_changed),
                "network_requests": len(network),
                "api_requests": sum(1 for n in network if n.is_api),
            }

            return InteractionProbe(
                schema_version=SCHEMA_VERSION,
                engine_version=__version__,
                probed_at=datetime.now(timezone.utc).isoformat(),
                url=url,
                final_url=final_url,
                title=title,
                config={"max_interactions": max_interactions,
                        "allow_list": sorted(ALLOW_LIST)},
                stats=stats,
                interactions=interactions,
                network=network,
            )
        finally:
            browser.close()
