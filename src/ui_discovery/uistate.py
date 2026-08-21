"""What a click revealed, and what on a settled page is worth its own picture.

A full-page screenshot of a URL shows the parts of a product that are always
there. It cannot show the modal behind "Add customer", the menu behind the
overflow button, or the panel behind the second tab — and those are usually
where the product actually is. This module names those states so the crawler
and the probe can capture them.

Two pure questions, no browser and no interaction of its own:

  `classify_state`     — a click happened and things appeared. What kind of
                         thing is it, what is it called, and which element
                         should be photographed?
  `component_targets`  — nothing was clicked. Which parts of this page deserve
                         a cropped picture of their own?

The browser work (clicking, screenshotting) stays in `crawler.py` and
`interactions.py`, where browser work already lives.
"""

from __future__ import annotations

from typing import Any, Optional

from .taxonomy import classify as classify_ui_type

# What a revealed container's `ui_type` means in the language a reader uses.
# The taxonomy already separates a modal from a drawer (`aria-modal`), which
# is a distinction that matters: one traps focus, the other does not.
_STATE_KINDS: dict[str, str] = {
    "dialog": "modal",
    "drawer": "drawer",
    "menu": "menu",
    "menubar": "menu",
    "tabpanel": "tab-panel",
    "tooltip": "tooltip",
    "popover": "popover",
    "listbox": "listbox",
    "tree": "tree",
    "disclosure": "disclosure",
}

# Element categories that can be a revealed *container* rather than a control
# inside one. A revealed <button> is content of the new state; a revealed
# [role=dialog] is the state itself.
_CONTAINER_CATEGORIES = frozenset({"dialog", "menu", "tab", "tooltip", "region"})

# Components worth a cropped picture on a page nobody has clicked yet, in the
# order a reader cares about them. `kind` is what the report calls it.
_COMPONENT_RULES: tuple[tuple[str, str], ...] = (
    ("form", "form"),
    ("dialog", "dialog"),
    ("drawer", "drawer"),
    ("tabpanel", "tab-panel"),
    ("table", "table"),
    ("grid", "table"),
    ("region", "region"),
)

# An icon is small in *both* directions; a component is not. The width floor is
# what excludes icons (they are rarely wider than ~48px); the height floor only
# has to exclude hairlines, so it is deliberately low. An unstyled single-row
# filter bar measures 1264x21 — wide, short, and exactly the kind of thing
# someone wants a picture of. Anything taller than the max is the whole page
# again, which the full-page screenshot already covers.
MIN_CROP_WIDTH_PX = 60
MIN_CROP_HEIGHT_PX = 16
MAX_CROP_HEIGHT_PX = 4000

# Per page. A table-heavy screen would otherwise emit hundreds of PNGs, and a
# capture nobody can open is not a capture.
MAX_COMPONENTS_PER_PAGE = 30


def _depth(dom_path: str) -> int:
    """How deep a selector sits. Used to prefer the outermost container: a
    revealed dialog and the revealed button inside it are both new, and it is
    the dialog we want to photograph."""
    return dom_path.count(">") + dom_path.count(">>>")


def _ui_type(el: dict) -> str:
    """The element's UI type.

    Raw extraction dicts do not carry one — `ui_type` is derived in Python by
    `taxonomy.classify`, which is a pure function of the same dict. Deriving it
    here keeps this module usable on a raw extraction (what the probe has in
    hand mid-interaction) as well as on an assembled `Page`.
    """
    return el.get("ui_type") or classify_ui_type(el) or ""


# Past this, a "name" is a paragraph. An unnamed container's textContent is
# everything inside it, so falling back to it produced headings like
# "What's New (V2.14.0)Version 2.14.0Aug 10, 2026What's New in ACME We've...".
MAX_NAME_CHARS = 60


def _name_of(el: dict) -> str:
    """A short, readable name, or "" when the element has none.

    Returning "" is deliberate: the caller falls back to the label of the
    control that opened the thing, which is short and meaningful. A truncated
    wall of body text is worse than no name at all — it is unreadable *and* it
    looks like a name.
    """
    name = (el.get("accessible_name") or "").strip()
    if name:
        return name[:MAX_NAME_CHARS]
    text = " ".join((el.get("text") or "").split())
    return text if 0 < len(text) <= MAX_NAME_CHARS else ""


def _box(el: dict) -> dict:
    return el.get("bounding_box") or {}


def _big_enough(el: dict) -> bool:
    box = _box(el)
    width, height = box.get("width", 0), box.get("height", 0)
    return (width >= MIN_CROP_WIDTH_PX
            and MIN_CROP_HEIGHT_PX <= height <= MAX_CROP_HEIGHT_PX)


def visible_paths(raw: dict) -> set[str]:
    """The `dom_path` of every element that was visible in an extraction.

    The baseline for "what appeared": an element already on the page but
    hidden counts as revealed when it becomes visible, which is exactly how
    most dialogs and menus work.
    """
    return {
        el.get("dom_path", "")
        for el in raw.get("elements", [])
        if el.get("visible") and el.get("dom_path")
    }


def revealed_elements(before_visible: set[str], after: dict) -> list[dict]:
    """Elements visible after an interaction that were not visible before."""
    return [
        el for el in after.get("elements", [])
        if el.get("visible")
        and el.get("dom_path")
        and el.get("dom_path") not in before_visible
    ]


def classify_state(
    trigger: dict,
    revealed: list[dict],
    *,
    after: Optional[dict] = None,
) -> Optional[dict]:
    """Identify the UI state a click opened, or None if nothing recognisable did.

    Returns `{"kind", "name", "dom_path"}`.

    Two sources, most authoritative first:

      1. The trigger's own `aria-controls` — the app saying, in standard
         markup, exactly which element this control opens. Nothing we could
         infer beats being told.
      2. The outermost revealed container. A dialog and the buttons inside it
         all appear at once; the dialog is the state, the buttons are its
         contents.

    Returns None when a click changed something that is not a nameable state —
    a table re-sorting, a counter ticking. That is a real outcome and is
    already recorded on the `Interaction`; inventing a "state" for it would
    fill the report with pictures of nothing.
    """
    if not revealed:
        return None

    by_path = {el.get("dom_path"): el for el in revealed}

    # 1. The app told us what this control opens.
    for path in trigger.get("controls") or []:
        target = by_path.get(path)
        if target is not None:
            found = _as_state(target, trigger)
            if found:
                return found
        # aria-controls can point at a container we did not capture as an
        # element (a plain <div> panel). It is still the right thing to
        # photograph, and the trigger's own affordance names the kind.
        if after is not None and path not in by_path:
            for el in after.get("elements", []):
                if el.get("dom_path") == path and el.get("visible"):
                    found = _as_state(el, trigger)
                    if found:
                        return found

    # 2. The outermost revealed container.
    containers = [
        el for el in revealed
        if el.get("category") in _CONTAINER_CATEGORIES
        and _ui_type(el) in _STATE_KINDS
    ]
    if containers:
        outermost = min(containers, key=lambda el: _depth(el.get("dom_path", "")))
        found = _as_state(outermost, trigger)
        if found:
            return found

    # 3. Something opened, and it identified itself only through the trigger:
    #    an `aria-expanded` button with a plain <div> panel. Common enough in
    #    real portals to be worth naming rather than discarding.
    attrs = trigger.get("attributes") or {}
    if attrs.get("aria-expanded") is not None and revealed:
        outermost = min(revealed, key=lambda el: _depth(el.get("dom_path", "")))
        return {
            "kind": "disclosure",
            "name": _name_of(trigger),
            "dom_path": outermost.get("dom_path", ""),
        }
    return None


def _as_state(container: dict, trigger: dict) -> Optional[dict]:
    kind = _STATE_KINDS.get(_ui_type(container))
    if not kind:
        return None
    return {
        "kind": kind,
        "name": _name_of(container) or _name_of(trigger),
        "dom_path": container.get("dom_path", ""),
    }


def state_signature(kind: str, trigger_label: str,
                    control_names: list[str]) -> tuple:
    """What makes two revealed states *the same* state.

    A repeated component opens the same thing once per instance: a grid of
    model cards each with a "Try out" button opens one Model Playground drawer,
    not thirty-seven of them. Photographing each is the same mistake as listing
    a table's "View" link once per row.

    A *labelled* trigger is the whole signature: "Try out" opening a drawer is
    one affordance however many cards carry it. Including what it revealed
    would defeat that, because the drawer shows the card's own data — the
    Model Playground for "GPT 5 mini" and for "GPT 5 nano" are the same
    component showing different rows.

    An *unlabelled* trigger has no such identity, and several icon-only
    buttons on one screen open genuinely different menus. There, the set of
    revealed controls is the only thing that tells them apart, so it joins the
    signature rather than collapsing them all into one.
    """
    label = " ".join((trigger_label or "").split()).lower()
    if label:
        return (kind, label)
    return (kind, "", frozenset(n for n in control_names[:8] if n))


def component_targets(raw: dict) -> list[dict]:
    """Parts of a settled page that deserve a picture of their own.

    Forms, open dialogs, visible tab panels, data tables and labelled regions:
    each is a thing a person would point at and name, and each is detectable
    from standard markup. Returns `{"kind", "dom_path", "name"}` per target,
    outermost first, deduplicated and capped.

    Deliberately absent: cards, widgets, tiles. They have no standard markup —
    `taxonomy.NOT_DETECTABLE` says so and says why — and guessing at class
    names is the framework-specific hack this engine does not do. A scope
    config can name them with a CSS selector, which is where site-specific
    knowledge belongs.
    """
    targets: list[dict] = []
    seen: set[str] = set()

    for ui_type, kind in _COMPONENT_RULES:
        for el in raw.get("elements", []):
            path = el.get("dom_path", "")
            if not path or path in seen:
                continue
            if _ui_type(el) != ui_type:
                continue
            if not el.get("visible") or not _big_enough(el):
                continue
            if ui_type == "region" and not _name_of(el):
                # An unnamed region is a <div>. Photographing it would produce
                # a picture nobody can label.
                continue
            seen.add(path)
            targets.append({
                "kind": kind,
                "dom_path": path,
                "name": _name_of(el) or f"(unnamed {kind})",
            })

    targets.sort(key=lambda t: _depth(t["dom_path"]))
    return targets[:MAX_COMPONENTS_PER_PAGE]


def state_filename(page_slug: str, index: int, kind: str) -> str:
    """A stable, readable name for a state screenshot."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in kind)[:24]
    return f"{page_slug}-{index:02d}-{safe}.png"


def component_filename(page_slug: str, index: int, kind: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in kind)[:24]
    return f"{page_slug}-{index:02d}-{safe}.png"


def summarize_states(states: list[Any]) -> dict[str, int]:
    """Counts by kind, for the report's headline."""
    out: dict[str, int] = {}
    for state in states:
        kind = getattr(state, "kind", None) or "unknown"
        out[kind] = out.get(kind, 0) + 1
    return out
