"""UI type taxonomy — what *kind* of control something is.

`Element.category` is the DOM-shape bucket the extractor sorted an element
into (button, link, input, …). It is what fingerprints and selectors are
built on, so it stays coarse and stable. `ui_type` is the second axis: the
kind of UI control a person would name — slider, tab, breadcrumb, file
upload, rich-text editor — which is what QA planning and inventory actually
need. "487 buttons" is not an actionable inventory; "28 disclosures, 8 tabs,
1 file upload, 1 rich-text editor" is.

Resolution order, most authoritative first:

    1. `aria-roledescription`  the app naming its own widget
    2. explicit `role=`        when the page author bothered to say
    3. input `type=`           range -> slider, file -> file-upload
    4. implicit element role   <nav>, <table>, <details>, <progress>, …
    5. state / behaviour       aria-expanded, aria-sort, contenteditable

Step 4 carries most of the weight, and it is the step a naive
"count the `role=` attributes" approach misses: a page can resolve to 22
distinct types while declaring only 8 roles, because HTML elements carry
their roles implicitly. Step 3 sits above it because an input's `type` is
more specific than any role an input can carry.

Everything here is a pure function over signals the extractor already
records — no browser, no interaction, no judgement, and nothing that knows
which framework built the page.
"""

from __future__ import annotations

from typing import Any, Optional

# --- the catalogue -----------------------------------------------------------
#
# Grouped for reporting. Membership is the contract: a type in here is one the
# engine claims it can recognise, so a capture reporting zero of it means the
# app genuinely has none — not that we forgot to look.

CATALOGUE: dict[str, tuple[str, ...]] = {
    "Structure & navigation": (
        "navigation", "breadcrumb", "sidebar", "banner", "contentinfo",
        "main", "search-landmark", "region", "toolbar", "menubar", "menu",
        "menuitem", "tablist", "tab", "tabpanel", "tree", "treeitem",
        "disclosure", "heading",
    ),
    "Overlays": (
        "dialog", "drawer", "tooltip", "popover",
    ),
    "Data": (
        "table", "grid", "columnheader", "sortable-column", "list",
        "listitem", "pagination",
    ),
    "Input": (
        "button", "link", "external-link", "download-link", "textbox",
        "password-input", "searchbox", "checkbox", "radio", "radiogroup",
        "combobox", "listbox", "option", "slider", "spinbutton",
        "date-input", "time-input", "color-picker", "file-upload",
        "rich-text-editor", "form", "fieldset",
    ),
    "Feedback & status": (
        "alert", "status", "live-region", "progressbar", "meter",
        "validation-message",
    ),
    "Media": (
        "image", "graphic", "canvas", "video", "audio", "iframe",
    ),
}

ALL_TYPES: frozenset[str] = frozenset(
    t for group in CATALOGUE.values() for t in group
)

# Types other inventories list that this engine deliberately does NOT claim,
# with the reason. Reported as a third bucket rather than silently absent —
# "we found no cards" and "we cannot detect cards" are different statements,
# and only one of them is about your application.
NOT_DETECTABLE: dict[str, str] = {
    "card": "no standard markup; detecting it means guessing at class names",
    "widget": "no standard markup; a dashboard block is a div like any other",
    "tag/chip/badge": "no standard markup; visually distinct, structurally not",
    "icon meaning": "what an icon signifies is judgement, not an observation",
    "permission group": "domain semantics, not a UI structure",
    "conditional branching": "requires varying form input, which mutates state",
    "sequential stage": "requires completing a multi-step flow",
}

# --- implicit roles ----------------------------------------------------------

_IMPLICIT_ROLE: dict[str, str] = {
    "a": "link", "button": "button", "nav": "navigation", "main": "main",
    "header": "banner", "footer": "contentinfo", "aside": "sidebar",
    "form": "form", "fieldset": "fieldset", "table": "table",
    "th": "columnheader", "img": "image", "select": "combobox",
    "textarea": "textbox", "dialog": "dialog", "ul": "list", "ol": "list",
    "li": "listitem", "details": "disclosure", "summary": "disclosure",
    "progress": "progressbar", "meter": "meter", "canvas": "canvas",
    "svg": "graphic", "video": "video", "audio": "audio", "iframe": "iframe",
    "option": "option", "datalist": "listbox",
    **{f"h{n}": "heading" for n in range(1, 7)},
}

_INPUT_TYPE: dict[str, str] = {
    "button": "button", "submit": "button", "reset": "button",
    "checkbox": "checkbox", "radio": "radio", "range": "slider",
    "number": "spinbutton", "search": "searchbox", "file": "file-upload",
    "date": "date-input", "datetime-local": "date-input", "month": "date-input",
    "week": "date-input", "time": "time-input", "color": "color-picker",
    "password": "password-input", "email": "textbox", "tel": "textbox",
    "url": "textbox", "text": "textbox",
}

# Explicit ARIA roles that map to a catalogue name different from the role.
_ROLE_ALIAS: dict[str, str] = {
    "alertdialog": "dialog", "complementary": "sidebar", "search": "search-landmark",
    "img": "image", "graphics-document": "graphic", "graphics-symbol": "graphic",
    "doc-pagelist": "pagination", "menuitemcheckbox": "menuitem",
    "menuitemradio": "menuitem", "switch": "checkbox", "textbox": "textbox",
}

_BREADCRUMB_HINTS = ("breadcrumb", "bread crumb")
_PAGINATION_HINTS = ("pagination", "pager", "paging")


def _attrs(element: dict[str, Any]) -> dict[str, str]:
    return element.get("attributes") or {}


def _named(element: dict[str, Any]) -> str:
    return " ".join(str(v) for v in (
        element.get("accessible_name") or "",
        _attrs(element).get("aria-label", ""),
        _attrs(element).get("class", ""),
    )).lower()


def _from_state(element: dict[str, Any]) -> Optional[str]:
    """Types carried by a state or behaviour attribute rather than a role."""
    attrs = _attrs(element)
    if attrs.get("contenteditable") in ("", "true"):
        return "rich-text-editor"
    if attrs.get("aria-sort"):
        return "sortable-column"
    if attrs.get("aria-invalid") == "true":
        return "validation-message"
    if attrs.get("aria-live"):
        # `alert`/`status` are more specific and handled by role; this is the
        # generic case of a region that announces its own changes.
        return "live-region"
    return None


def classify(element: dict[str, Any]) -> Optional[str]:
    """The UI type of one extracted element, or None if it has no type we
    recognise. Pure: takes the extractor's raw dict, touches nothing else."""
    attrs = _attrs(element)

    # 1. The app naming its own widget wins — it is the only source here that
    #    knows what the thing is *for*, and it is app-authored, not inferred.
    described = (attrs.get("aria-roledescription") or "").strip().lower()
    if described:
        return described[:40]

    tag = (element.get("tag") or "").strip().lower()

    # 2. A role the *page author* wrote. Note this reads the attribute, not
    #    `element["role"]`: the extractor pre-computes that field and flattens
    #    every exotic input to "textbox", which would hide file uploads, date
    #    pickers and colour pickers behind a generic role.
    role = (attrs.get("role") or "").strip().split(" ")[0].lower()

    # 3. Input type — more specific than any role an input can carry.
    if not role and tag == "input":
        role = _INPUT_TYPE.get((attrs.get("type") or "text").lower(), "textbox")

    # 4. The role the element carries implicitly.
    if not role:
        role = _IMPLICIT_ROLE.get(tag, "")

    # 5. Last resort: whatever the extractor derived.
    if not role:
        role = (element.get("role") or "").strip().lower()

    resolved = _ROLE_ALIAS.get(role, role)

    # Refinements that a bare role cannot express.
    if resolved == "navigation":
        haystack = _named(element)
        if any(h in haystack for h in _BREADCRUMB_HINTS):
            return "breadcrumb"
        if any(h in haystack for h in _PAGINATION_HINTS):
            return "pagination"
    if resolved == "dialog" and attrs.get("aria-modal") != "true":
        # A non-modal dialog is the drawer / slide-over / side panel pattern.
        # The distinction matters: one traps focus, the other does not.
        return "drawer"
    if resolved == "link":
        if attrs.get("download") is not None:
            return "download-link"
        if attrs.get("target") == "_blank":
            return "external-link"
    if resolved == "combobox" and tag == "select":
        return "combobox"
    if resolved == "columnheader" and attrs.get("aria-sort"):
        # A column header that advertises its sort state is a control, not
        # just a label — and it is one a test will want to exercise.
        return "sortable-column"

    if resolved in ALL_TYPES:
        return resolved

    # 5. Nothing role-shaped; fall back to state signals.
    state = _from_state(element)
    if state:
        return state
    # `aria-expanded` on something with no other identity is a disclosure.
    if attrs.get("aria-expanded") is not None:
        return "disclosure"
    if attrs.get("aria-haspopup"):
        return "menu"
    return None


# --- coverage ----------------------------------------------------------------

def coverage(types_found: dict[str, int]) -> dict[str, Any]:
    """Split the catalogue into found / absent, and attach the types this
    engine does not claim at all.

    Three buckets, not two, because "this app has no sliders" and "we cannot
    see sliders" are different findings and only the first is about the app.
    """
    found = {t: n for t, n in types_found.items() if t in ALL_TYPES and n}
    # Types the app declared for itself (aria-roledescription) are real
    # findings even though they are not in our catalogue.
    app_declared = {t: n for t, n in types_found.items()
                    if t not in ALL_TYPES and n}
    absent = sorted(ALL_TYPES - set(found))
    return {
        "found": dict(sorted(found.items(), key=lambda kv: -kv[1])),
        "app_declared": dict(sorted(app_declared.items(), key=lambda kv: -kv[1])),
        "absent": absent,
        "not_detectable": dict(NOT_DETECTABLE),
        "catalogue_size": len(ALL_TYPES),
        "found_count": len(found),
    }
