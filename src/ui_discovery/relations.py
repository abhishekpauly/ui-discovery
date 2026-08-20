"""How the pieces of a UI connect — screen to screen, and element to element.

The engine has always been able to say *what* is on a screen. That is an
inventory, and an inventory does not tell you what a product is. What does is
the connections: that Customers reaches Customer Detail when you click a name
in the table; that the "Delivery speed" radios are one choice, not three
controls; that the Status dropdown offers Open / In progress / Closed; that
the Summary tab owns the panel below it.

Every relationship here is *computed* from signals the extractor already
records — `parent_path`, `controls`, `owner_form`, `group`, `described_by`,
`columns`, and the labelled navigation edges the crawler builds. Nothing here
touches a browser, re-crawls, or guesses: if the markup does not say two things
are related, this module does not claim they are.

Pure functions over a `Crawl`, in the shape of `analysis/` — importable and
composable, with the CLI a thin wrapper (CLAUDE.md principle 9).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from . import SCHEMA_VERSION, __version__
from .models import (
    Crawl,
    Element,
    ElementLink,
    FormField,
    FormGroup,
    NavEdge,
    Page,
    PageNode,
    Relations,
    ScreenRelations,
    TableGroup,
)

# Categories that hold a value a person supplies.
FIELD_CATEGORIES = frozenset({"input", "select", "textarea"})


def _label(el: Element) -> str:
    """The best human-readable name for an element, or an honest placeholder.

    An unnamed control is a finding, not a gap to paper over: it is invisible
    to a screen reader too. Saying so is more useful than inventing a name.
    """
    name = (el.accessible_name or "").strip()
    if name:
        return name
    text = (el.text or "").strip()
    if text and len(text) <= 60:
        return text
    return f"(unnamed {el.ui_type or el.category})"


def _is_unnamed(el: Element) -> bool:
    return not (el.accessible_name or "").strip() and not (el.text or "").strip()


# --- screen -> screen --------------------------------------------------------


def screen_edges(crawl: Crawl) -> list[NavEdge]:
    """Every labelled navigation edge in the crawl.

    Reads `Crawl.navigation`, which the crawler now writes with the label of
    the control that reaches each target. Snapshots taken before that carry
    only `from`/`to`, and degrade to unlabelled edges rather than failing —
    an old capture is still readable, just less informative.
    """
    edges: list[NavEdge] = []
    for raw in crawl.navigation:
        source, target = raw.get("from"), raw.get("to")
        if not source or not target:
            continue
        edges.append(NavEdge(
            source=source,
            target=target,
            label=raw.get("label", "") or "",
            region=raw.get("region") or None,
            control=raw.get("control", "link") or "link",
        ))
    return edges


def _edges_by_end(edges: Iterable[NavEdge]) -> tuple[dict, dict]:
    inbound: dict[str, list[NavEdge]] = defaultdict(list)
    outbound: dict[str, list[NavEdge]] = defaultdict(list)
    for edge in edges:
        # A screen linking to itself is real markup (a "Home" link on Home)
        # but says nothing about how screens connect, so it is left out of
        # the relationship view.
        if edge.source == edge.target:
            continue
        outbound[edge.source].append(edge)
        inbound[edge.target].append(edge)
    return inbound, outbound


# --- element -> element ------------------------------------------------------


def _by_path(page: Page) -> dict[str, Element]:
    return {el.dom_path: el for el in page.elements if el.dom_path}


def element_links(page: Page) -> list[ElementLink]:
    """The relationships between elements on one screen.

    Three kinds, each from a different standard signal, and each a genuine
    link between two elements that are both in this page's inventory:

      contains   the captured-element tree, via `parent_path`
      controls   `aria-controls` / `aria-owns` — tab to panel, button to dialog
      groups     `owner_form` — the fields that belong to a form

    Labelling and description are deliberately *not* here. The `<label>` and
    the help-text `<span>` are not interactive, so they are not in the element
    inventory, and inventing a link to something we never captured would leave
    a dangling reference. Those facts live on the element itself
    (`accessible_name_source`, `described_by`) and are surfaced per field by
    `forms_of`. Table columns likewise live on `TableGroup.columns`.

    Both ends of every link are `dom_path`s of captured elements, so nothing
    dangles.
    """
    by_path = _by_path(page)
    links: list[ElementLink] = []

    def add(kind: str, source: Element, target_path: str) -> None:
        target = by_path.get(target_path)
        if target is None or target is source:
            return
        links.append(ElementLink(
            kind=kind,
            source=source.dom_path,
            target=target_path,
            source_label=_label(source),
            target_label=_label(target),
        ))

    for el in page.elements:
        if el.parent_path:
            # Recorded parent -> child, which is the direction a reader walks.
            parent = by_path.get(el.parent_path)
            if parent is not None:
                links.append(ElementLink(
                    kind="contains",
                    source=el.parent_path,
                    target=el.dom_path,
                    source_label=_label(parent),
                    target_label=_label(el),
                ))
        for target_path in el.controls:
            add("controls", el, target_path)
        if el.owner_form:
            owner = by_path.get(el.owner_form)
            if owner is not None:
                links.append(ElementLink(
                    kind="groups",
                    source=el.owner_form,
                    target=el.dom_path,
                    source_label=_label(owner),
                    target_label=_label(el),
                ))
    return links


# --- forms -------------------------------------------------------------------


def reading_order(elements: list[Element]) -> list[Element]:
    """Elements in the order a person meets them: top to bottom, left to right.

    Extraction walks the DOM one category at a time — every button, then every
    link, then every input — which is right for the extractor and wrong for a
    reader. It put the Status dropdown after the checkbox in a form where it
    sits fourth. Geometry is the honest answer to "what order is this form in",
    and it is already captured.

    Elements with no geometry keep their original relative position rather
    than being shuffled to the front.
    """
    def key(pair):
        index, el = pair
        box = el.bounding_box
        if box is None:
            return (1, 0.0, 0.0, index)
        return (0, round(box.y), round(box.x), index)

    return [el for _, el in sorted(enumerate(elements), key=key)]


def describe_field(el: Element) -> FormField:
    options = [o.label for o in el.options if o.label]
    default = el.value
    if el.ui_type in ("checkbox", "radio"):
        # A checkbox's `value` attribute is "on" whether or not it is ticked,
        # which is exactly the wrong thing to print in a "Default" column.
        default = "checked" if el.states.get("checked") == "true" else "unchecked"
    if not default:
        selected = [o.label for o in el.options if o.selected]
        default = ", ".join(selected) if selected else None
    return FormField(
        label=_label(el),
        ui_type=el.ui_type or el.category,
        dom_path=el.dom_path,
        required=el.states.get("required") == "true",
        placeholder=el.attributes.get("placeholder"),
        help_text=el.described_by,
        options=options,
        option_count=el.option_count,
        default=default,
        group=el.group,
        enabled=el.enabled,
    )


def _merge_radio_groups(fields: list[FormField]) -> list[FormField]:
    """Collapse a set of radios into the single choice they actually are.

    Three radios named `speed` are one question with three answers, and
    listing them as three separate fields is how a form of eight questions
    gets documented as a form of twenty controls.
    """
    out: list[FormField] = []
    seen: dict[str, FormField] = {}
    for field in fields:
        if field.ui_type != "radio" or not field.group:
            out.append(field)
            continue
        existing = seen.get(field.group)
        if existing is None:
            merged = field.model_copy(update={
                "label": field.group,
                "ui_type": "radio-group",
                "options": [field.label],
                "option_count": 1,
                "default": field.label if field.default else None,
            })
            seen[field.group] = merged
            out.append(merged)
            continue
        existing.options.append(field.label)
        existing.option_count += 1
        if field.default and not existing.default:
            existing.default = field.label
        # A group is required if any of its members is.
        existing.required = existing.required or field.required
    return out


def forms_of(page: Page) -> list[FormGroup]:
    """Every form on a screen, with its fields and its submit actions.

    Fields with no owning `<form>` are not dropped: plenty of real UIs put a
    search box or a filter row outside any form element, and those are still
    inputs a person fills in. They are collected under a single "(ungrouped
    inputs)" entry so the report can show them without claiming a form exists.
    """
    by_path = _by_path(page)
    grouped: dict[str, list[Element]] = defaultdict(list)
    loose: list[Element] = []

    for el in reading_order(page.elements):
        if el.category not in FIELD_CATEGORIES:
            continue
        if el.category == "input" and el.attributes.get("type") == "hidden":
            continue
        if el.owner_form and el.owner_form in by_path:
            grouped[el.owner_form].append(el)
        else:
            loose.append(el)

    # Submit-ish actions belong to the form they sit in.
    actions: dict[str, list[str]] = defaultdict(list)
    for el in reading_order(page.elements):
        if el.category != "button" or not el.owner_form:
            continue
        actions[el.owner_form].append(_label(el))

    forms: list[FormGroup] = []
    for path, elements in grouped.items():
        form_el = by_path[path]
        forms.append(FormGroup(
            name=_label(form_el) if not _is_unnamed(form_el) else "(unnamed form)",
            dom_path=path,
            region=form_el.landmark,
            fields=_merge_radio_groups([describe_field(e) for e in elements]),
            actions=actions.get(path, []),
            screenshot=form_el.clip_screenshot,
        ))
    if loose:
        forms.append(FormGroup(
            name="(ungrouped inputs)",
            fields=_merge_radio_groups([describe_field(e) for e in loose]),
        ))
    return forms


# --- tables ------------------------------------------------------------------


def tables_of(page: Page) -> list[TableGroup]:
    """Every data table, with its columns and the actions repeated in its rows.

    Row actions are found by containment rather than by geometry: a control
    whose `parent_path` chain reaches the table is in the table.
    """
    by_path = _by_path(page)
    tables = [el for el in page.elements if el.category == "table"]
    if not tables:
        return []
    table_paths = {el.dom_path for el in tables}

    def owning_table(el: Element) -> Optional[str]:
        seen: set[str] = set()
        path = el.parent_path
        while path and path not in seen:
            if path in table_paths:
                return path
            seen.add(path)
            parent = by_path.get(path)
            if parent is None:
                return None
            path = parent.parent_path
        return None

    row_actions: dict[str, list[str]] = defaultdict(list)
    for el in page.elements:
        if el.category not in ("button", "link"):
            continue
        owner = owning_table(el)
        if owner is None:
            continue
        label = _label(el)
        # The same action repeats once per row; report it once.
        if label not in row_actions[owner]:
            row_actions[owner].append(label)

    return [
        TableGroup(
            name=_label(el) if not _is_unnamed(el) else "(unnamed table)",
            dom_path=el.dom_path,
            region=el.landmark,
            columns=el.columns,
            row_count=el.row_count,
            row_actions=row_actions.get(el.dom_path, []),
            screenshot=el.clip_screenshot,
        )
        for el in tables
    ]


# --- assembly ----------------------------------------------------------------


def page_relations(
    node: PageNode,
    inbound: Optional[list[NavEdge]] = None,
    outbound: Optional[list[NavEdge]] = None,
) -> ScreenRelations:
    """Everything relational about one captured screen."""
    return ScreenRelations(
        url=node.url,
        title=node.page.title,
        depth=node.depth,
        inbound=list(inbound or []),
        outbound=list(outbound or []),
        forms=forms_of(node.page),
        tables=tables_of(node.page),
        element_links=element_links(node.page),
    )


def build_relations(crawl: Crawl) -> Relations:
    """The whole relationship model for a crawl. Pure — no filesystem access."""
    edges = screen_edges(crawl)
    inbound, outbound = _edges_by_end(edges)

    screens = [
        page_relations(node, inbound.get(node.url), outbound.get(node.url))
        for node in crawl.pages
    ]

    # Where the crawl actually began: depth 0 is assigned by BFS from the start
    # URL, so this is the front door regardless of whether other screens happen
    # to link back to it (they usually do — that is what a "Home" link is).
    entry_points = [n.url for n in crawl.pages if n.depth == 0]
    if not entry_points and crawl.pages:
        entry_points = [crawl.pages[0].url]

    # A screen nothing links to, that is not the front door, was reached only
    # because it was seeded or clicked into. Worth naming: ordinary
    # link-following would never find it, so neither would a user browsing.
    orphans = [
        n.url for n in crawl.pages
        if not inbound.get(n.url) and n.url not in entry_points
    ]

    stats = {
        "screens": len(screens),
        "navigation_edges": len(edges),
        "labelled_edges": sum(1 for e in edges if e.label),
        "element_links": sum(len(s.element_links) for s in screens),
        "forms": sum(len(s.forms) for s in screens),
        "form_fields": sum(len(f.fields) for s in screens for f in s.forms),
        "tables": sum(len(s.tables) for s in screens),
        "orphan_screens": len(orphans),
    }

    return Relations(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_crawl_id=crawl.crawl_id,
        start_url=crawl.config.start_url,
        stats=stats,
        entry_points=entry_points,
        orphans=orphans,
        screens=screens,
    )
