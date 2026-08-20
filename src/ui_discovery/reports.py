"""Render human-readable reports FROM the structured `Crawl` model.

Reports are a presentation layer. The JSON model is the source of truth; these
renderers never hold information that isn't in it.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

from .relations import build_relations

from .models import (
    Analysis,
    Crawl,
    Documentation,
    Diff,
    InteractionProbe,
    PageNode,
    QAPlan,
    Relations,
    Semantics,
)


def _ui_totals(crawl: Crawl) -> Counter:
    totals: Counter = Counter()
    for node in crawl.pages:
        for k, v in node.page.counts.items():
            if k in ("visible_elements", "total_elements"):
                continue
            totals[k] += v
    return totals


def _page_label(node: PageNode) -> str:
    title = node.page.title or "(untitled)"
    return f"{title} — {node.url}"


# H2 keys carried per page when the crawl ran with --probe.
_PROBE_STAT_KEYS = (
    "elements_seen", "executed", "observed_only", "blocked", "caution",
    "state_changing", "network_requests", "api_requests",
)


def _probe_totals(crawl: Crawl) -> dict[str, int]:
    """Sum each page's probe stats. Empty dict when the crawl wasn't probed,
    which is what suppresses the probe sections from the report."""
    probes = [n.probe for n in crawl.pages if n.probe]
    if not probes:
        return {}
    return {
        key: sum(p.stats.get(key, 0) for p in probes)
        for key in _PROBE_STAT_KEYS
    }


def _api_endpoints(crawl: Crawl, limit: int = 20) -> list[tuple[str, int]]:
    """Distinct API endpoint patterns observed across all probed pages, most
    frequent first."""
    counter: Counter = Counter()
    for node in crawl.pages:
        if not node.probe:
            continue
        for req in node.probe.network:
            if req.is_api and req.endpoint_pattern:
                counter[req.endpoint_pattern] += 1
    return counter.most_common(limit)


# --- Reading a capture ------------------------------------------------------
#
# Everything below renders the same facts twice, Markdown and HTML, from one
# set of helpers. The helpers exist so the two cannot drift: a fact that
# appears in the HTML and not the Markdown is a fact someone will miss.
#
# The organising principle is that a report is read by a person who has never
# seen the portal. Counts do not help that person; names, pictures and
# connections do.


def _shot_rel(path: str | None) -> str:
    """A screenshot path relative to the report, whatever folder it is in."""
    if not path:
        return ""
    p = Path(path)
    parent = p.parent.name
    if parent in ("components", "states"):
        return f"screenshots/{parent}/{p.name}"
    return f"screenshots/{p.name}"


def _n(count: int, singular: str, plural: str | None = None) -> str:
    """"1 screen" / "4 screens". A report that says "1 screens" reads like it
    was generated, which is the impression this whole rewrite exists to undo."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"


def _short(url: str, limit: int = 48) -> str:
    """A URL trimmed to its distinguishing tail, for use inside a table cell."""
    trimmed = url.split("://", 1)[-1]
    return trimmed if len(trimmed) <= limit else "…" + trimmed[-(limit - 1):]


def _screen_title(node: PageNode) -> str:
    return node.page.title or "(untitled)"


def _titles_by_url(crawl: Crawl) -> dict[str, str]:
    return {n.url: _screen_title(n) for n in crawl.pages}


def _actions(page) -> list:
    """Every control on a screen a person can act on, with the label they read.

    The safety class is included because it is the single most useful thing to
    know about an action before you touch it, and the engine has already
    decided it — showing it costs nothing and answers "what on this screen is
    destructive?" without anyone having to click to find out.
    """
    from .safety import classify_label

    from .relations import reading_order

    out = []
    for el in reading_order(page.elements):
        if el.category not in ("button", "tab", "menu", "disclosure"):
            continue
        # A tab panel is where a tab takes you, and a tablist is the strip the
        # tabs sit in. Neither is something you click, and listing them
        # alongside the tabs themselves says the same thing three times.
        if el.ui_type in ("tabpanel", "tablist", "menubar"):
            continue
        label = (el.accessible_name or el.text or "").strip()
        if not label:
            continue
        state = []
        if not el.enabled:
            state.append("disabled")
        for key in ("expanded", "selected", "pressed", "checked"):
            if key in el.states:
                state.append(f"{key}={el.states[key]}")
        options = " / ".join(o.label for o in el.options[:8] if o.label)
        out.append({
            "label": label,
            "type": el.ui_type or el.category,
            "region": el.landmark or "",
            "state": ", ".join(state),
            "safety": classify_label(label),
            "options": options,
        })
    # One row per distinct control, not one per instance: a "View" button on
    # forty table rows is one affordance.
    seen, unique = set(), []
    for row in out:
        key = (row["label"], row["type"], row["region"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _nav_menus(page) -> list:
    """The navigation menus on a screen, each with the items it offers."""
    menus: dict[str, list[str]] = {}
    for el in page.elements:
        if el.category != "link" or el.landmark != "navigation":
            continue
        label = (el.accessible_name or el.text or "").strip()
        if not label:
            continue
        menus.setdefault("Navigation", [])
        if label not in menus["Navigation"]:
            menus["Navigation"].append(label)
    return [{"name": name, "items": items} for name, items in menus.items()]


def _screen_states(node: PageNode) -> list:
    return list(node.probe.states) if node.probe else []


def _screen_apis(node: PageNode, limit: int = 15) -> list:
    if not node.probe:
        return []
    seen, out = set(), []
    for req in node.probe.network:
        if not req.is_api:
            continue
        key = (req.method, req.endpoint_pattern or req.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(req)
        if len(out) >= limit:
            break
    return out


def _relations_by_url(relations: Relations) -> dict:
    return {s.url: s for s in relations.screens}


MERMAID_SCREEN_LIMIT = 40


def _mermaid(crawl: Crawl, relations: Relations) -> list[str]:
    """A site map: screens as boxes, the control you click as the edge label.

    Capped, because a diagram of 200 screens is not a diagram. Past the cap the
    table below it is the complete record, and the report says so rather than
    quietly drawing a partial picture.
    """
    titles = _titles_by_url(crawl)
    urls = [n.url for n in crawl.pages][:MERMAID_SCREEN_LIMIT]
    if len(urls) < 2:
        return []
    ids = {url: f"S{i}" for i, url in enumerate(urls)}

    def clean(text: str) -> str:
        return (text.replace('"', "'").replace("[", "(").replace("]", ")")
                .replace("|", "/").replace("\n", " ")[:40])

    lines = ["```mermaid", "graph LR"]
    for url in urls:
        lines.append(f'  {ids[url]}["{clean(titles.get(url) or _short(url))}"]')
    drawn = set()
    for edge in relations.screens:
        for out in edge.outbound:
            if out.source not in ids or out.target not in ids:
                continue
            key = (out.source, out.target, out.label)
            if key in drawn:
                continue
            drawn.add(key)
            label = clean(out.label)
            arrow = f'-- "{label}" -->' if label else "-->"
            lines.append(f"  {ids[out.source]} {arrow} {ids[out.target]}")
    lines.append("```")
    return lines


def _scoping_notes(crawl: Crawl) -> list[str]:
    """What this capture chose not to do, in plain words.

    Without this, a scoped-down run is indistinguishable from a thorough one:
    a module nobody probed looks like a module with no modals, and a tab nobody
    opened looks like a tab that does not exist. Both are claims about the
    product, and neither is true.
    """
    notes: list[str] = []
    for entry in crawl.config.probe_profiles:
        scope = entry.get("scope", "(default)")
        where = "by default" if scope == "(default)" else f"under `{scope}`"
        if not entry.get("enabled", True):
            notes.append(f"Screens {where} were **read but never clicked** "
                         f"(`probe.enabled: false`), so no modals, menus or "
                         f"tab panels there were opened.")
            continue
        tabs = entry.get("tabs", "all")
        if tabs == "none":
            notes.append(f"Tabs on screens {where} were recorded but never "
                         f"opened (`probe.tabs: none`).")
        elif tabs == "listed":
            listed = ", ".join(entry.get("tab_labels") or []) or "none"
            notes.append(f"Only these tabs were opened {where}: {listed} "
                         f"(`probe.tabs: listed`). Any other tab exists but "
                         f"was not looked into.")
        excluded = entry.get("tab_exclude") or []
        if excluded:
            notes.append(f"These tabs were explicitly never opened {where}: "
                         + ", ".join(excluded) + ".")
        if not entry.get("state_capture", True):
            notes.append(f"Modals and panels {where} were opened but not "
                         f"photographed (`probe.state_capture: false`).")
        if not entry.get("component_screenshots", True):
            notes.append(f"Forms, dialogs and tables {where} were not "
                         f"cropped into their own screenshots.")
    return notes


def _refused_by_config(crawl: Crawl) -> list[str]:
    """Controls the safety gates allowed but the config declined to open."""
    seen: list[str] = []
    for node in crawl.pages:
        if not node.probe:
            continue
        for i in node.probe.interactions:
            if i.skipped_reason == "tab excluded by config" and i.target:
                if i.target not in seen:
                    seen.append(i.target)
    return seen


# --- Markdown ---------------------------------------------------------------


def build_markdown(crawl: Crawl, relations: Relations | None = None) -> str:
    relations = relations if relations is not None else build_relations(crawl)
    c = crawl.config
    s = crawl.stats
    rel_by_url = _relations_by_url(relations)
    titles = _titles_by_url(crawl)
    rstats = relations.stats
    lines: list[str] = []

    lines.append(f"# UI Crawl Report — {c.start_url}")
    lines.append("")
    lines.append(f"*Crawl `{crawl.crawl_id}` · engine {crawl.engine_version} · "
                 f"schema {crawl.schema_version} · captured {crawl.finished_at}*")
    lines.append("")

    # --- what this is ------------------------------------------------------
    lines.append("## What this capture contains")
    lines.append("")
    endpoints = _api_endpoints(crawl, limit=1000)
    states_total = sum(len(_screen_states(n)) for n in crawl.pages)
    shots = c.capabilities.get("screenshots", True)
    lines.append(
        f"**{_n(s.pages_crawled, 'screen')}** of the application at "
        f"`{c.start_url}`, with "
        f"{_n(rstats.get('form_fields', 0), 'form field')} across "
        f"{_n(rstats.get('forms', 0), 'form')}, "
        f"{_n(rstats.get('tables', 0), 'data table')}, and "
        f"{_n(rstats.get('navigation_edges', 0), 'navigation path')} between "
        f"screens. Every screen below lists the controls it offers and how you "
        f"reach it"
        + (", with a screenshot." if shots else " (screenshots were off).")
    )
    lines.append("")
    if relations.entry_points:
        lines.append("- **Start here:** "
                     + ", ".join(f"{titles.get(u, u)} (`{_short(u)}`)"
                                 for u in relations.entry_points))
    lines.append(f"- Screens captured: **{s.pages_crawled}** "
                 f"(failed: {s.pages_failed}) · crawl depth ≤ {c.max_depth}")
    lines.append(f"- Navigation paths: {rstats.get('navigation_edges', 0)} "
                 f"({rstats.get('labelled_edges', 0)} with a readable label)")
    lines.append(f"- Element relationships recorded: "
                 f"{rstats.get('element_links', 0)}")
    if states_total:
        lines.append(f"- Modals, menus and panels opened and photographed: "
                     f"**{states_total}**")
    if endpoints:
        lines.append(f"- API endpoints observed behind the UI: "
                     f"{_n(len(endpoints), 'endpoint')}")
    lines.append(f"- Runtime: {s.runtime_seconds}s "
                 f"({crawl.started_at} → {crawl.finished_at})")
    lines.append("")

    if s.auth_expired:
        lines.append(f"> ⚠️ **Session rejected.** Of {s.pages_crawled} crawled "
                     f"pages, {s.pages_logged_out} look logged-out and "
                     f"{s.pages_empty} rendered nothing — despite a saved "
                     f"session being supplied. This capture is of the "
                     f"login/blank state, not the product. Re-capture the "
                     f"session and crawl again.")
        lines.append("")
    elif s.pages_logged_out or s.pages_empty:
        lines.append(f"> ℹ️ {s.pages_logged_out} page(s) look logged-out and "
                     f"{s.pages_empty} rendered nothing. No session was "
                     f"supplied, so this may be expected; pass `--auth-state` "
                     f"to capture the signed-in product.")
        lines.append("")

    # --- site map ----------------------------------------------------------
    lines.append("## Site map")
    lines.append("")
    diagram = _mermaid(crawl, relations)
    if diagram:
        lines.extend(diagram)
        lines.append("")
        if s.pages_crawled > MERMAID_SCREEN_LIMIT:
            lines.append(f"_Showing the first {MERMAID_SCREEN_LIMIT} of "
                         f"{s.pages_crawled} screens. The table below is "
                         f"complete._")
            lines.append("")
    else:
        lines.append("_Only one screen was captured, so there is no map to "
                     "draw._")
        lines.append("")

    lines.append("### Page graph")
    lines.append("")
    lines.append("Pages by depth from the start URL:")
    lines.append("")
    for node in crawl.pages:
        indent = "  " * (node.depth or 0)
        n_el = node.page.counts.get("total_elements", 0)
        lines.append(f"{indent}- {_screen_title(node)} — {node.url}  "
                     f"_(depth {node.depth}, {n_el} elements)_")
    lines.append("")

    # --- how screens connect ----------------------------------------------
    lines.append("## How the screens connect")
    lines.append("")
    lines.append("Each row reads: to get *to* this screen, click that control "
                 "on that screen.")
    lines.append("")
    lines.append("| Screen | Reached by clicking | Leads to |")
    lines.append("| --- | --- | --- |")
    for node in crawl.pages:
        screen = rel_by_url.get(node.url)
        if screen is None:
            continue
        inbound = "<br>".join(
            f"“{e.label or '(unlabelled)'}” on {titles.get(e.source, _short(e.source))}"
            for e in screen.inbound[:5]
        ) or "_nothing links here_"
        outbound = "<br>".join(
            f"“{e.label or '(unlabelled)'}” → {titles.get(e.target, _short(e.target))}"
            for e in screen.outbound[:5]
        ) or "—"
        extra_in = (f"<br>_+{len(screen.inbound) - 5} more_"
                    if len(screen.inbound) > 5 else "")
        extra_out = (f"<br>_+{len(screen.outbound) - 5} more_"
                     if len(screen.outbound) > 5 else "")
        lines.append(f"| **{_screen_title(node)}**<br>`{_short(node.url)}` | "
                     f"{inbound}{extra_in} | {outbound}{extra_out} |")
    lines.append("")
    if relations.orphans:
        lines.append(f"> **{len(relations.orphans)} screen(s) nothing links "
                     f"to.** Reached only because they were seeded or clicked "
                     f"into — ordinary link-following would never find them, "
                     f"so neither would someone browsing: "
                     + ", ".join(f"`{_short(u)}`" for u in relations.orphans[:10]))
        lines.append("")

    # --- inventory ---------------------------------------------------------
    lines.append("## UI inventory (all pages)")
    lines.append("")
    for k, v in sorted(_ui_totals(crawl).items(), key=lambda kv: -kv[1]):
        lines.append(f"- {k}: {v}")
    lines.append("")

    totals = _probe_totals(crawl)
    if totals:
        lines.append("## Interaction & network probe (all pages)")
        lines.append("")
        lines.append(f"- Controls seen: {totals['elements_seen']} · "
                     f"executed: **{totals['executed']}** · "
                     f"observed only: {totals['observed_only']}")
        lines.append(f"- Refused as destructive (BLOCK): {totals['blocked']} · "
                     f"caution: {totals['caution']}")
        lines.append(f"- State-changing interactions: {totals['state_changing']}")
        if states_total:
            kinds = Counter(st.kind for n in crawl.pages
                            for st in _screen_states(n))
            lines.append("- States captured: "
                         + ", ".join(f"{k} ({v})" for k, v in kinds.most_common()))
        lines.append(f"- Network requests: {totals['network_requests']} "
                     f"(API: {totals['api_requests']})")
        lines.append("")
        top = _api_endpoints(crawl)
        if top:
            lines.append("Observed API endpoints:")
            lines.append("")
            for pattern, count in top:
                lines.append(f"- `{pattern}` ({count})")
            lines.append("")

    # --- the screens themselves -------------------------------------------
    lines.append("## Screens")
    lines.append("")
    for index, node in enumerate(crawl.pages, start=1):
        lines.extend(_screen_markdown(index, node, rel_by_url.get(node.url),
                                      titles))

    # --- honesty -----------------------------------------------------------
    lines.append("## Not captured")
    lines.append("")
    inv_missed = sorted(
        {edge["to"] for edge in crawl.navigation} - {n.url for n in crawl.pages})
    if inv_missed:
        lines.append(f"- **{len(inv_missed)} screen(s) were discovered but "
                     f"never visited** — the page budget ran out. Raise "
                     f"`--max-pages` and re-run.")
    if c.unmarked_clickables and not c.deep_nav:
        lines.append(f"- {c.unmarked_clickables} element(s) are clickable but "
                     f"were never marked up as links, so link-following "
                     f"cannot see where they go. Re-run with `--deep-nav`.")
    if not c.probe:
        lines.append("- No interaction probe ran, so no modals, menus or tab "
                     "panels were opened and no API traffic was observed. "
                     "Re-run without `--no-probe`.")
    for note in _scoping_notes(crawl):
        lines.append(f"- {note}")
    refused = _refused_by_config(crawl)
    if refused:
        lines.append("- Tabs present but not opened, by configuration: "
                     + ", ".join(f"“{t}”" for t in refused[:20])
                     + ". They exist; this capture did not look inside them.")
    lines.append("- **Cards, tiles, widgets, badges and icon meaning are not "
                 "detectable** from standard markup — they are `<div>`s like "
                 "any other, and guessing at class names would be a "
                 "framework-specific hack. Their absence here says nothing "
                 "about your product. Name them with "
                 "`capabilities.component_selectors` in a scope config to have "
                 "them photographed.")
    lines.append("")

    return "\n".join(lines)


def _screen_markdown(index: int, node: PageNode, screen, titles: dict) -> list[str]:
    """One screen, described the way documentation would describe it."""
    p = node.page
    lines: list[str] = []
    lines.append(f"### {index}. {_screen_title(node)}")
    lines.append("")
    lines.append(f"`{node.url}` · depth {node.depth} · "
                 f"HTTP {p.readiness.get('http_status')}")
    lines.append("")
    if p.screenshot_path:
        lines.append(f"![{_screen_title(node)}]({_shot_rel(p.screenshot_path)})")
        lines.append("")

    headings = [h.text for h in p.headings if h.text][:8]
    if headings:
        lines.append("**What's on this screen:** " + " · ".join(headings))
        lines.append("")

    if screen is not None:
        if screen.inbound:
            lines.append("**How you get here:** " + "; ".join(
                f"click “{e.label or '(unlabelled)'}” on "
                f"{titles.get(e.source, _short(e.source))}"
                for e in screen.inbound[:4]))
            lines.append("")
        if screen.outbound:
            lines.append("**Where it leads:** " + "; ".join(
                f"“{e.label or '(unlabelled)'}” → "
                f"{titles.get(e.target, _short(e.target))}"
                for e in screen.outbound[:8]))
            lines.append("")

    for menu in _nav_menus(p):
        lines.append(f"**{menu['name']}:** " + " · ".join(menu["items"][:15]))
        lines.append("")

    actions = _actions(p)
    if actions:
        lines.append("**Actions available**")
        lines.append("")
        lines.append("| Control | Type | Region | State | Safety |")
        lines.append("| --- | --- | --- | --- | --- |")
        for a in actions[:40]:
            label = a["label"]
            if a["options"]:
                label += f"<br>_options: {a['options']}_"
            lines.append(f"| {label} | {a['type']} | {a['region'] or '—'} | "
                         f"{a['state'] or '—'} | {a['safety']} |")
        if len(actions) > 40:
            lines.append(f"| _+{len(actions) - 40} more_ | | | | |")
        lines.append("")

    if screen is not None:
        for form in screen.forms:
            if not form.fields:
                continue
            lines.append(f"**Form — {form.name}**")
            lines.append("")
            if form.screenshot:
                lines.append(f"![{form.name}]({_shot_rel(form.screenshot)})")
                lines.append("")
            lines.append("| Field | Type | Required | Options | Default | Help |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for f in form.fields:
                options = " / ".join(f.options[:10])
                if f.option_count > 10:
                    options += f" _(+{f.option_count - 10} more)_"
                lines.append(
                    f"| {f.label} | {f.ui_type} | "
                    f"{'**yes**' if f.required else 'no'} | "
                    f"{options or '—'} | {f.default or '—'} | "
                    f"{f.help_text or f.placeholder or '—'} |")
            lines.append("")
            if form.actions:
                lines.append("Submitted by: "
                             + ", ".join(f"“{a}”" for a in form.actions))
                lines.append("")

        for table in screen.tables:
            lines.append(f"**Data table — {table.name}**")
            lines.append("")
            if table.screenshot:
                lines.append(f"![{table.name}]({_shot_rel(table.screenshot)})")
                lines.append("")
            lines.append(f"- Columns: "
                         + (", ".join(table.columns) if table.columns
                            else "_none declared_"))
            lines.append(f"- Rows captured: {table.row_count}")
            if table.row_actions:
                lines.append("- Per-row actions: "
                             + ", ".join(f"“{a}”" for a in table.row_actions))
            lines.append("")

    states = _screen_states(node)
    if states:
        lines.append("**Modals, menus and panels on this screen**")
        lines.append("")
        lines.append("_Not visible until something is clicked — each was "
                     "opened safely and photographed._")
        lines.append("")
        for state in states:
            lines.append(f"- **{state.name or '(unnamed)'}** "
                         f"({state.kind}) — opens when you click "
                         f"“{state.trigger_label}”")
            if state.screenshot:
                lines.append(f"  <br>![{state.name}]"
                             f"({_shot_rel(state.screenshot)})")
            controls = [c.accessible_name or c.text for c in state.controls
                        if (c.accessible_name or c.text)]
            if controls:
                lines.append("  - Contains: "
                             + ", ".join(f"“{c}”" for c in controls[:12]))
            for f in state.fields:
                options = " / ".join(f.options[:8])
                lines.append(f"  - Field “{f.label}” ({f.ui_type})"
                             + (f", options: {options}" if options else "")
                             + (" — required" if f.required else ""))
        lines.append("")

    if node.probe:
        # Stated per screen even when it found nothing: "the probe ran here and
        # nothing opened" and "the probe never ran here" are different facts,
        # and a reader cannot tell them apart from silence.
        ps = node.probe.stats
        lines.append(
            f"**Interaction probe:** {ps.get('executed', 0)} control(s) "
            f"exercised, {ps.get('blocked', 0)} refused as destructive, "
            f"{ps.get('states_captured', 0)} panel(s) opened, "
            f"{ps.get('api_requests', 0)} API call(s) observed.")
        lines.append("")

    apis = _screen_apis(node)
    if apis:
        lines.append("**API calls this screen makes**")
        lines.append("")
        for req in apis:
            lines.append(f"- `{req.method}` {req.endpoint_pattern or req.url}"
                         + (f" → {req.status}" if req.status else ""))
        lines.append("")

    return lines


# --- HTML -------------------------------------------------------------------


def _esc(x: object) -> str:
    return html.escape(str(x))


def _thumb(path: str | None, alt: str, height: int = 160) -> str:
    rel = _shot_rel(path)
    if not rel:
        return ""
    return (f'<a href="{_esc(rel)}" class="shot"><img src="{_esc(rel)}" '
            f'alt="{_esc(alt)}" loading="lazy" style="max-height:{height}px"></a>')


def _table(headers: list[str], rows: list[list[str]], empty: str = "None") -> str:
    if not rows:
        return f'<p class="meta">{_esc(empty)}</p>'
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                   for row in rows)
    # Wide tables scroll inside their own box rather than pushing the page out.
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def _screen_html(index: int, node: PageNode, screen, titles: dict) -> str:
    p = node.page
    title = _screen_title(node)
    parts: list[str] = []

    parts.append(f'<h3 id="screen-{index}">{index}. {_esc(title)}</h3>')
    parts.append(f'<p class="meta"><code>{_esc(node.url)}</code> · depth '
                 f'{_esc(node.depth)} · HTTP '
                 f'{_esc(p.readiness.get("http_status"))}</p>')
    if p.screenshot_path:
        parts.append(_thumb(p.screenshot_path, title, height=260))

    headings = [h.text for h in p.headings if h.text][:8]
    if headings:
        parts.append("<p><b>What's on this screen:</b> "
                     + _esc(" · ".join(headings)) + "</p>")

    if screen is not None and screen.inbound:
        parts.append("<p><b>How you get here:</b> " + "; ".join(
            f'click “{_esc(e.label or "(unlabelled)")}” on '
            f'{_esc(titles.get(e.source, _short(e.source)))}'
            for e in screen.inbound[:4]) + "</p>")
    if screen is not None and screen.outbound:
        parts.append("<p><b>Where it leads:</b> " + "; ".join(
            f'“{_esc(e.label or "(unlabelled)")}” → '
            f'{_esc(titles.get(e.target, _short(e.target)))}'
            for e in screen.outbound[:8]) + "</p>")

    for menu in _nav_menus(p):
        parts.append(f'<p><b>{_esc(menu["name"])}:</b> '
                     + _esc(" · ".join(menu["items"][:15])) + "</p>")

    actions = _actions(p)
    if actions:
        parts.append("<h4>Actions available</h4>")
        rows = []
        for a in actions[:60]:
            label = f"<b>{_esc(a['label'])}</b>"
            if a["options"]:
                label += (f'<br><span class="meta">options: '
                          f'{_esc(a["options"])}</span>')
            rows.append([
                label, _esc(a["type"]), _esc(a["region"] or "—"),
                _esc(a["state"] or "—"),
                f'<span class="sev {_esc(a["safety"].lower())}">'
                f'{_esc(a["safety"])}</span>',
            ])
        parts.append(_table(["Control", "Type", "Region", "State", "Safety"], rows))

    if screen is not None:
        for form in screen.forms:
            if not form.fields:
                continue
            parts.append(f"<h4>Form — {_esc(form.name)}</h4>")
            if form.screenshot:
                parts.append(_thumb(form.screenshot, form.name))
            rows = []
            for f in form.fields:
                options = " / ".join(f.options[:10])
                if f.option_count > 10:
                    options += f" (+{f.option_count - 10} more)"
                rows.append([
                    _esc(f.label), _esc(f.ui_type),
                    "<b>yes</b>" if f.required else "no",
                    _esc(options or "—"), _esc(f.default or "—"),
                    _esc(f.help_text or f.placeholder or "—"),
                ])
            parts.append(_table(
                ["Field", "Type", "Required", "Options", "Default", "Help"], rows))
            if form.actions:
                parts.append('<p class="meta">Submitted by: '
                             + _esc(", ".join(form.actions)) + "</p>")

        for tbl in screen.tables:
            parts.append(f"<h4>Data table — {_esc(tbl.name)}</h4>")
            if tbl.screenshot:
                parts.append(_thumb(tbl.screenshot, tbl.name))
            parts.append(
                f'<p class="meta">Columns: '
                f'{_esc(", ".join(tbl.columns) or "none declared")} · '
                f'{_esc(tbl.row_count)} row(s) captured'
                + (f' · per-row actions: {_esc(", ".join(tbl.row_actions))}'
                   if tbl.row_actions else "")
                + "</p>")

    states = _screen_states(node)
    if states:
        parts.append("<h4>Modals, menus and panels</h4>")
        parts.append('<p class="meta">Not visible until something is clicked '
                     "— each was opened safely and photographed.</p>")
        for state in states:
            controls = [c.accessible_name or c.text for c in state.controls
                        if (c.accessible_name or c.text)]
            fields = "".join(
                f"<li>Field “{_esc(f.label)}” ({_esc(f.ui_type)})"
                + (f", options: {_esc(' / '.join(f.options[:8]))}"
                   if f.options else "")
                + (" — required" if f.required else "") + "</li>"
                for f in state.fields
            )
            parts.append(
                '<div class="state">'
                f'<p><b>{_esc(state.name or "(unnamed)")}</b> '
                f'<span class="pill">{_esc(state.kind)}</span><br>'
                f'<span class="meta">opens when you click '
                f'“{_esc(state.trigger_label)}”</span></p>'
                + _thumb(state.screenshot, state.name or state.kind)
                + (f'<p class="meta">Contains: '
                   f'{_esc(", ".join(c for c in controls[:12]))}</p>'
                   if controls else "")
                + (f"<ul>{fields}</ul>" if fields else "")
                + "</div>")

    if node.probe:
        ps = node.probe.stats
        parts.append(
            f'<p><b>Interaction probe:</b> {_esc(ps.get("executed", 0))} '
            f'control(s) exercised, {_esc(ps.get("blocked", 0))} refused as '
            f'destructive, {_esc(ps.get("states_captured", 0))} panel(s) '
            f'opened, {_esc(ps.get("api_requests", 0))} API call(s) '
            f'observed.</p>')

    apis = _screen_apis(node)
    if apis:
        parts.append("<h4>API calls this screen makes</h4>")
        parts.append(_table(
            ["Method", "Endpoint", "Status"],
            [[_esc(r.method), f"<code>{_esc(r.endpoint_pattern or r.url)}</code>",
              _esc(r.status or "—")] for r in apis]))

    return ('<details class="screen" open>'
            f'<summary>{index}. {_esc(title)} '
            f'<span class="meta">— {_esc(_short(node.url))}</span></summary>'
            + "".join(parts) + "</details>")


_REPORT_CSS = """
  :root { color-scheme: light dark;
          --bg:#ffffff; --fg:#1a1a1a; --muted:#5b6169; --line:#e5e7eb;
          --panel:#f7f7f8; --accent:#1d4ed8; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --fg:#e7e9ee; --muted:#a0a6b0; --line:#2a2e36;
            --panel:#1c1f25; --accent:#8ab4ff; }
  }
  body { font: 15px/1.6 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0 auto; padding: 2rem 1.25rem; max-width: 1040px;
         background: var(--bg); color: var(--fg); }
  h1 { font-size: 1.7rem; margin: 0 0 .25rem; }
  h2 { margin-top: 2.5rem; font-size: 1.25rem;
       border-bottom: 1px solid var(--line); padding-bottom: .3rem; }
  h3 { margin: 0 0 .25rem; font-size: 1.1rem; }
  h4 { margin: 1.4rem 0 .3rem; font-size: .95rem; color: var(--muted);
       text-transform: uppercase; letter-spacing: .04em; }
  a { color: var(--accent); }
  code { background: var(--panel); padding: 1px 5px; border-radius: 3px;
         font-size: 12px; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; margin: .4rem 0 .8rem; }
  th, td { text-align: left; padding: 7px 10px; font-size: 13px;
           border-bottom: 1px solid var(--line); vertical-align: top; }
  th { background: var(--panel); font-weight: 600; }
  .meta { color: var(--muted); font-size: 13px; }
  .kpis span { display:inline-block; background: var(--panel);
               border-radius: 6px; padding: 6px 10px; margin: 3px;
               font-size: 13px; }
  .banner { padding: 10px 12px; border-radius: 6px; border-left: 4px solid;
            background: var(--panel); }
  .banner.error { border-color:#b91c1c; }
  .banner.note { border-color:#9ca3af; }
  .lead { font-size: 1.05rem; }
  details.screen { border: 1px solid var(--line); border-radius: 8px;
                   padding: .75rem 1rem; margin: 1rem 0;
                   background: var(--panel); }
  details.screen > summary { cursor: pointer; font-weight: 600;
                             margin: -.25rem -.25rem .25rem; }
  details.screen > summary h3 { display: inline; }
  .shot img { border: 1px solid var(--line); border-radius: 6px;
              margin: .35rem .35rem .35rem 0; max-width: 100%; }
  .state { border-left: 3px solid var(--line); padding-left: .8rem;
           margin: .6rem 0; }
  .pill { background: var(--bg); border: 1px solid var(--line);
          border-radius: 999px; padding: 1px 8px; font-size: 11px;
          color: var(--muted); }
  .sev { font-size: 11px; font-weight: 700; }
  .sev.block { color: #b91c1c; } .sev.caution { color: #b45309; }
  .sev.safe { color: #15803d; }
  nav.toc { background: var(--panel); border-radius: 8px; padding: .6rem 1rem; }
  nav.toc ol { margin: .3rem 0; padding-left: 1.3rem; }
  nav.toc li { font-size: 13px; }
"""


def build_html(crawl: Crawl, relations: Relations | None = None) -> str:
    relations = relations if relations is not None else build_relations(crawl)
    c, s = crawl.config, crawl.stats
    rel_by_url = _relations_by_url(relations)
    titles = _titles_by_url(crawl)
    rstats = relations.stats
    esc = _esc

    states_total = sum(len(_screen_states(n)) for n in crawl.pages)
    endpoints = _api_endpoints(crawl, limit=1000)
    shots = c.capabilities.get("screenshots", True)

    # H4: a banner, not a footnote — a crawl of login screens that reports
    # "42 pages captured" is worse than useless if the reader misses why.
    auth_html = ""
    if s.auth_expired:
        auth_html = (
            f'<p class="banner error"><b>Session rejected.</b> Of '
            f'{esc(s.pages_crawled)} crawled pages, '
            f'{esc(s.pages_logged_out)} look logged-out and '
            f'{esc(s.pages_empty)} rendered nothing — despite a saved session '
            f'being supplied. This capture is of the login/blank state, not '
            f'the product.</p>'
        )
    elif s.pages_logged_out or s.pages_empty:
        auth_html = (
            f'<p class="banner note">{esc(s.pages_logged_out)} page(s) look '
            f'logged-out and {esc(s.pages_empty)} rendered nothing. No session '
            f'was supplied, so this may be expected; pass '
            f'<code>--auth-state</code> to capture the signed-in product.</p>'
        )

    toc = "".join(
        f'<li><a href="#screen-{i}">{esc(_screen_title(n))}</a></li>'
        for i, n in enumerate(crawl.pages, start=1)
    )

    entry_html = ""
    if relations.entry_points:
        entry_html = ("<p><b>Start here:</b> " + ", ".join(
            f'{esc(titles.get(u, u))} (<code>{esc(_short(u))}</code>)'
            for u in relations.entry_points) + "</p>")

    diagram = _mermaid(crawl, relations)
    if diagram:
        # Artifacts render mermaid natively from a <pre class="mermaid"> block;
        # elsewhere it degrades to readable source rather than to nothing.
        body = "\n".join(diagram[1:-1])
        map_html = f'<pre class="mermaid">{esc(body)}</pre>'
        if s.pages_crawled > MERMAID_SCREEN_LIMIT:
            map_html += (f'<p class="meta">Showing the first '
                         f'{MERMAID_SCREEN_LIMIT} of {esc(s.pages_crawled)} '
                         f'screens. The table below is complete.</p>')
    else:
        map_html = ('<p class="meta">Only one screen was captured, so there is '
                    "no map to draw.</p>")

    connect_rows = []
    for node in crawl.pages:
        screen = rel_by_url.get(node.url)
        if screen is None:
            continue
        inbound = "<br>".join(
            f'“{esc(e.label or "(unlabelled)")}” on '
            f'{esc(titles.get(e.source, _short(e.source)))}'
            for e in screen.inbound[:5]) or '<span class="meta">nothing links here</span>'
        outbound = "<br>".join(
            f'“{esc(e.label or "(unlabelled)")}” → '
            f'{esc(titles.get(e.target, _short(e.target)))}'
            for e in screen.outbound[:5]) or "—"
        connect_rows.append([
            f'<b>{esc(_screen_title(node))}</b><br>'
            f'<code>{esc(_short(node.url))}</code>',
            inbound, outbound,
        ])

    orphan_html = ""
    if relations.orphans:
        orphan_html = (
            f'<p class="banner note"><b>{esc(len(relations.orphans))} screen(s) '
            f'nothing links to.</b> Reached only because they were seeded or '
            f'clicked into — ordinary link-following would never find them, so '
            f'neither would someone browsing: '
            + ", ".join(f"<code>{esc(_short(u))}</code>"
                        for u in relations.orphans[:10]) + "</p>")

    totals = _ui_totals(crawl)
    totals_str = " · ".join(f"{esc(k)}: <b>{esc(v)}</b>"
                            for k, v in sorted(totals.items(), key=lambda kv: -kv[1]))

    # H2: probe sections appear only when the crawl actually probed.
    probe_html = ""
    pt = _probe_totals(crawl)
    if pt:
        top = _api_endpoints(crawl)
        endpoints_html = ""
        if top:
            endpoints_html = "<h3>Observed API endpoints</h3>" + _table(
                ["Endpoint pattern", "Requests"],
                [[f"<code>{esc(p)}</code>", esc(n)] for p, n in top])
        kinds = Counter(st.kind for n in crawl.pages for st in _screen_states(n))
        states_line = ("".join(f"<span>{esc(k)}: <b>{esc(v)}</b></span>"
                               for k, v in kinds.most_common())
                       if kinds else "")
        probe_html = f"""
<h2>Interaction &amp; network probe</h2>
<div class="kpis">
  <span>Controls seen: <b>{esc(pt['elements_seen'])}</b></span>
  <span>Executed: <b>{esc(pt['executed'])}</b></span>
  <span>Observed only: <b>{esc(pt['observed_only'])}</b></span>
  <span>Refused (destructive): <b>{esc(pt['blocked'])}</b></span>
  <span>Caution: <b>{esc(pt['caution'])}</b></span>
  <span>State-changing: <b>{esc(pt['state_changing'])}</b></span>
  <span>Requests: <b>{esc(pt['network_requests'])}</b> (API {esc(pt['api_requests'])})</span>
</div>
<p class="meta">Only structurally-safe, reversible controls are executed;
destructive ones are observed and refused.</p>
{'<div class="kpis">' + states_line + '</div>' if states_line else ''}
{endpoints_html}"""

    missed = sorted(
        {edge["to"] for edge in crawl.navigation} - {n.url for n in crawl.pages})
    limits = []
    if missed:
        limits.append(f"<li><b>{esc(len(missed))} screen(s) were discovered but "
                      f"never visited</b> — the page budget ran out. Raise "
                      f"<code>--max-pages</code> and re-run.</li>")
    if c.unmarked_clickables and not c.deep_nav:
        limits.append(f"<li>{esc(c.unmarked_clickables)} element(s) are "
                      f"clickable but were never marked up as links. Re-run "
                      f"with <code>--deep-nav</code>.</li>")
    if not c.probe:
        limits.append("<li>No interaction probe ran, so no modals, menus or "
                      "tab panels were opened and no API traffic was observed. "
                      "Re-run without <code>--no-probe</code>.</li>")
    for note in _scoping_notes(crawl):
        limits.append(f"<li>{esc(note)}</li>")
    refused = _refused_by_config(crawl)
    if refused:
        limits.append("<li>Tabs present but not opened, by configuration: "
                      + esc(", ".join(refused[:20]))
                      + ". They exist; this capture did not look inside "
                      "them.</li>")
    limits.append(
        "<li><b>Cards, tiles, widgets, badges and icon meaning are not "
        "detectable</b> from standard markup — they are <code>&lt;div&gt;</code>s "
        "like any other, and guessing at class names would be a "
        "framework-specific hack. Their absence here says nothing about your "
        "product. Name them with <code>capabilities.component_selectors</code> "
        "in a scope config to have them photographed.</li>")

    screens_html = "".join(
        _screen_html(i, n, rel_by_url.get(n.url), titles)
        for i, n in enumerate(crawl.pages, start=1)
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UI Crawl Report — {esc(c.start_url)}</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>UI Crawl Report</h1>
<p class="meta">Start <code>{esc(c.start_url)}</code> · crawl
 <code>{esc(crawl.crawl_id)}</code> · engine {esc(crawl.engine_version)}
 · schema {esc(crawl.schema_version)} · captured {esc(crawl.finished_at)}</p>
{auth_html}

<h2>What this capture contains</h2>
<p class="lead"><b>{esc(_n(s.pages_crawled, 'screen'))}</b> of the application
 at <code>{esc(c.start_url)}</code>, with
 {esc(_n(rstats.get('form_fields', 0), 'form field'))} across
 {esc(_n(rstats.get('forms', 0), 'form'))},
 {esc(_n(rstats.get('tables', 0), 'data table'))}, and
 {esc(_n(rstats.get('navigation_edges', 0), 'navigation path'))} between
 screens. Every screen below lists the controls it offers and how you reach
 it{', with a screenshot.' if shots else ' (screenshots were off).'}</p>
{entry_html}
<div class="kpis">
  <span>Screens: <b>{esc(s.pages_crawled)}</b></span>
  <span>Failed: <b>{esc(s.pages_failed)}</b></span>
  <span>Navigation paths: <b>{esc(rstats.get('navigation_edges', 0))}</b>
   ({esc(rstats.get('labelled_edges', 0))} labelled)</span>
  <span>Element relationships: <b>{esc(rstats.get('element_links', 0))}</b></span>
  <span>Forms: <b>{esc(rstats.get('forms', 0))}</b></span>
  <span>Tables: <b>{esc(rstats.get('tables', 0))}</b></span>
  {f'<span>States captured: <b>{esc(states_total)}</b></span>' if states_total else ''}
  {f'<span>API endpoints: <b>{esc(len(endpoints))}</b></span>' if endpoints else ''}
  <span>Runtime: <b>{esc(s.runtime_seconds)}s</b></span>
</div>

<h2>Site map</h2>
{map_html}
<nav class="toc"><b>Screens</b><ol>{toc}</ol></nav>

<h2>How the screens connect</h2>
<p class="meta">Each row reads: to get <i>to</i> this screen, click that
 control on that screen.</p>
{_table(["Screen", "Reached by clicking", "Leads to"], connect_rows)}
{orphan_html}

<h2>UI inventory (all pages)</h2>
<p>{totals_str}</p>
{probe_html}

<h2>Screens</h2>
{screens_html}

<h2>Not captured</h2>
<ul>{''.join(limits)}</ul>
</body></html>"""


# --- V2: analysis reports ---------------------------------------------------


def build_analysis_markdown(analysis: Analysis) -> str:
    lines: list[str] = []
    lines.append(f"# UI Analysis — {analysis.start_url}")
    lines.append("")
    lines.append(f"*From crawl `{analysis.source_crawl_id}` · engine "
                 f"{analysis.engine_version} · schema {analysis.schema_version}*")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    for k, v in analysis.stats.items():
        lines.append(f"- {k.replace('_', ' ')}: {v}")
    lines.append("")

    lines.append("## Navigation menus")
    lines.append("")
    if not analysis.navigations:
        lines.append("_None detected._")
    for nav in analysis.navigations:
        tag = " (breadcrumb)" if nav.is_breadcrumb else ""
        label = nav.label or "(unlabeled)"
        lines.append(f"- **{label}**{tag} — {len(nav.items)} items, "
                     f"on {nav.page_count} page(s): "
                     + " › ".join(nav.items))
    lines.append("")

    shared = [c for c in analysis.components if c.kind == "shared"]
    repeated = [c for c in analysis.components if c.kind == "repeated"]

    lines.append(f"## Shared components ({len(shared)})")
    lines.append("")
    lines.append("_Controls that recur across pages — the app's global chrome._")
    lines.append("")
    for c in shared[:40]:
        loc = f" in `{c.landmark}`" if c.landmark else ""
        lines.append(f"- “{c.label}” ({c.role or c.category}){loc} — "
                     f"on {c.page_count} pages ({c.instance_count} instances)")
    lines.append("")

    lines.append(f"## Repeated components ({len(repeated)})")
    lines.append("")
    lines.append("_Shapes that repeat within pages — list/table instances._")
    lines.append("")
    for c in repeated[:40]:
        loc = f" in `{c.landmark}`" if c.landmark else ""
        lines.append(f"- {c.role or c.category}{loc} — {c.instance_count} "
                     f"instances across {c.page_count} page(s) "
                     f"(signature `{c.signature}`)")
    lines.append("")

    lines.append("## Regions by page")
    lines.append("")
    for pa in analysis.pages:
        region_str = ", ".join(f"{r.type} ({r.element_count})" for r in pa.regions)
        lines.append(f"- **{pa.title or pa.url}** _(depth {pa.depth})_: {region_str}")
    lines.append("")

    return "\n".join(lines)


def build_analysis_html(analysis: Analysis) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    shared = [c for c in analysis.components if c.kind == "shared"]
    repeated = [c for c in analysis.components if c.kind == "repeated"]

    kpis = "".join(
        f"<span>{esc(k.replace('_', ' '))}: <b>{esc(v)}</b></span>"
        for k, v in analysis.stats.items()
    )

    nav_rows = "".join(
        f"<tr><td>{esc(n.label or '(unlabeled)')}"
        f"{' <em>breadcrumb</em>' if n.is_breadcrumb else ''}</td>"
        f"<td>{esc(n.page_count)}</td>"
        f"<td>{esc(' › '.join(n.items))}</td></tr>"
        for n in analysis.navigations
    ) or '<tr><td colspan="3">None detected</td></tr>'

    shared_rows = "".join(
        f"<tr><td>{esc(c.label)}</td><td>{esc(c.role or c.category)}</td>"
        f"<td>{esc(c.landmark or '')}</td><td>{esc(c.page_count)}</td>"
        f"<td>{esc(c.instance_count)}</td></tr>"
        for c in shared[:60]
    ) or '<tr><td colspan="5">None</td></tr>'

    repeated_rows = "".join(
        f"<tr><td>{esc(c.role or c.category)}</td><td>{esc(c.landmark or '')}</td>"
        f"<td>{esc(c.instance_count)}</td><td>{esc(c.page_count)}</td>"
        f"<td><code>{esc(c.signature)}</code></td></tr>"
        for c in repeated[:60]
    ) or '<tr><td colspan="5">None</td></tr>'

    region_rows = "".join(
        f"<tr><td>{esc(pa.title or pa.url)}</td><td>{esc(pa.depth)}</td>"
        f"<td>{esc(', '.join(f'{r.type} ({r.element_count})' for r in pa.regions))}</td></tr>"
        for pa in analysis.pages
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>UI Analysis — {esc(analysis.start_url)}</title>
<style>
  body {{ font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 980px; color: #1a1a1a; }}
  h1 {{ font-size: 1.5rem; }} h2 {{ margin-top: 2rem; font-size: 1.15rem; }}
  code {{ background:#f4f4f5; padding:1px 4px; border-radius:3px; font-size:12px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top:.5rem; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid #eee;
          font-size:13px; vertical-align:top; }}
  th {{ background:#fafafa; }}
  .kpis span {{ display:inline-block; background:#f4f4f5; border-radius:6px;
               padding:6px 10px; margin:3px; font-size:13px; }}
  .meta {{ color:#666; font-size:13px; }}
</style></head><body>
<h1>UI Analysis</h1>
<p class="meta">Start <code>{esc(analysis.start_url)}</code> · from crawl
 <code>{esc(analysis.source_crawl_id)}</code> · schema {esc(analysis.schema_version)}</p>
<div class="kpis">{kpis}</div>

<h2>Navigation menus</h2>
<table><thead><tr><th>Menu</th><th>Pages</th><th>Items</th></tr></thead>
<tbody>{nav_rows}</tbody></table>

<h2>Shared components ({esc(len(shared))})</h2>
<p class="meta">Controls that recur across pages — the app's global chrome.</p>
<table><thead><tr><th>Label</th><th>Role</th><th>Region</th><th>Pages</th><th>Instances</th></tr></thead>
<tbody>{shared_rows}</tbody></table>

<h2>Repeated components ({esc(len(repeated))})</h2>
<p class="meta">Shapes that repeat within pages — list / table instances.</p>
<table><thead><tr><th>Role</th><th>Region</th><th>Instances</th><th>Pages</th><th>Signature</th></tr></thead>
<tbody>{repeated_rows}</tbody></table>

<h2>Regions by page</h2>
<table><thead><tr><th>Page</th><th>Depth</th><th>Regions (element count)</th></tr></thead>
<tbody>{region_rows}</tbody></table>
</body></html>"""


def write_analysis(analysis: Analysis, output_dir: str) -> dict[str, str]:
    """Write analysis.json + analysis.md + analysis.html into `output_dir`."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "analysis.json"),
        "markdown": str(out / "analysis.md"),
        "html": str(out / "analysis.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(analysis.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["markdown"]).write_text(
        build_analysis_markdown(analysis), encoding="utf-8"
    )
    Path(paths["html"]).write_text(build_analysis_html(analysis), encoding="utf-8")
    return paths


# --- V3: interaction probe reports ------------------------------------------


def build_probe_markdown(probe: InteractionProbe) -> str:
    lines: list[str] = []
    lines.append(f"# Interaction Probe — {probe.title or probe.url}")
    lines.append("")
    lines.append(f"*`{probe.url}` · engine {probe.engine_version} · "
                 f"schema {probe.schema_version}*")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for k, v in probe.stats.items():
        lines.append(f"- {k.replace('_', ' ')}: {v}")
    lines.append(f"- allow-list: {', '.join(probe.config.get('allow_list', []))}")
    lines.append("")

    executed = [i for i in probe.interactions if i.executed]
    lines.append(f"## Executed interactions ({len(executed)})")
    lines.append("")
    lines.append("_Only structurally-safe, reversible controls were clicked._")
    lines.append("")
    for i in executed:
        effects = []
        if i.route_changed:
            effects.append("route changed")
        if i.dialog_opened:
            effects.append("dialog opened")
        if i.expanded_changed:
            effects.append("expanded toggled")
        if not effects and i.dom_changed:
            effects.append("DOM changed")
        if not effects:
            effects.append("no observable change")
        rev = " · reverted" if i.reverted else ""
        lines.append(f"- **{i.target}** ({i.interaction_type}) → "
                     f"{', '.join(effects)}{rev}")
    lines.append("")

    blocked = [i for i in probe.interactions
               if not i.executed and i.safety_label in ("BLOCK", "CAUTION")]
    lines.append(f"## Refused for safety ({len(blocked)})")
    lines.append("")
    lines.append("_Recorded but never clicked — destructive or state-changing._")
    lines.append("")
    for i in blocked:
        lines.append(f"- **{i.target}** ({i.interaction_type}) — "
                     f"{i.safety_label}: {i.skipped_reason}")
    lines.append("")

    if probe.network:
        api = [n for n in probe.network if n.is_api]
        lines.append(f"## Network — API calls ({len(api)} of "
                     f"{len(probe.network)} requests)")
        lines.append("")
        for n in api[:40]:
            gql = " [graphql]" if n.is_graphql else ""
            lines.append(f"- `{n.method}` {n.endpoint_pattern} → "
                         f"{n.status}{gql}")
        lines.append("")
    return "\n".join(lines)


def build_probe_html(probe: InteractionProbe) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    kpis = "".join(
        f"<span>{esc(k.replace('_', ' '))}: <b>{esc(v)}</b></span>"
        for k, v in probe.stats.items()
    )

    def eff(i) -> str:
        e = []
        if i.route_changed:
            e.append("route")
        if i.dialog_opened:
            e.append("dialog")
        if i.expanded_changed:
            e.append("expand")
        if not e and i.dom_changed:
            e.append("dom")
        return ", ".join(e) or "—"

    exec_rows = "".join(
        f"<tr><td>{esc(i.target)}</td><td>{esc(i.interaction_type)}</td>"
        f"<td>{esc(eff(i))}</td><td>{'yes' if i.reverted else 'no'}</td></tr>"
        for i in probe.interactions if i.executed
    ) or '<tr><td colspan="4">None</td></tr>'

    refused_rows = "".join(
        f"<tr><td>{esc(i.target)}</td><td>{esc(i.interaction_type)}</td>"
        f"<td>{esc(i.safety_label)}</td><td>{esc(i.skipped_reason)}</td></tr>"
        for i in probe.interactions
        if not i.executed and i.safety_label in ("BLOCK", "CAUTION")
    ) or '<tr><td colspan="4">None</td></tr>'

    net_rows = "".join(
        f"<tr><td>{esc(n.method)}</td><td>{esc(n.endpoint_pattern)}</td>"
        f"<td>{esc(n.status)}</td><td>{esc(n.resource_type)}</td>"
        f"<td>{'✓' if n.is_api else ''}{' gql' if n.is_graphql else ''}</td></tr>"
        for n in probe.network[:60]
    ) or '<tr><td colspan="5">None</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Interaction Probe — {esc(probe.url)}</title>
<style>
  body {{ font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 960px; color:#1a1a1a; }}
  h1 {{ font-size:1.5rem; }} h2 {{ margin-top:2rem; font-size:1.15rem; }}
  code {{ background:#f4f4f5; padding:1px 4px; border-radius:3px; font-size:12px; }}
  table {{ border-collapse:collapse; width:100%; margin-top:.5rem; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid #eee;
          font-size:13px; vertical-align:top; }}
  th {{ background:#fafafa; }}
  .kpis span {{ display:inline-block; background:#f4f4f5; border-radius:6px;
               padding:6px 10px; margin:3px; font-size:13px; }}
  .meta {{ color:#666; font-size:13px; }}
  .safe {{ color:#166534; }}
</style></head><body>
<h1>Interaction Probe</h1>
<p class="meta"><code>{esc(probe.url)}</code> · schema {esc(probe.schema_version)}
 · allow-list: {esc(', '.join(probe.config.get('allow_list', [])))}</p>
<div class="kpis">{kpis}</div>

<h2>Executed interactions</h2>
<p class="meta safe">Only structurally-safe, reversible controls were clicked.</p>
<table><thead><tr><th>Target</th><th>Type</th><th>Effect</th><th>Reverted</th></tr></thead>
<tbody>{exec_rows}</tbody></table>

<h2>Refused for safety</h2>
<p class="meta">Recorded but never clicked — destructive or state-changing.</p>
<table><thead><tr><th>Target</th><th>Type</th><th>Label</th><th>Reason</th></tr></thead>
<tbody>{refused_rows}</tbody></table>

<h2>Network</h2>
<table><thead><tr><th>Method</th><th>Endpoint</th><th>Status</th><th>Type</th><th>API</th></tr></thead>
<tbody>{net_rows}</tbody></table>
</body></html>"""


def write_probe(probe: InteractionProbe, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "probe.json"),
        "markdown": str(out / "probe.md"),
        "html": str(out / "probe.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(probe.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["markdown"]).write_text(build_probe_markdown(probe), encoding="utf-8")
    Path(paths["html"]).write_text(build_probe_html(probe), encoding="utf-8")
    return paths


# --- V5: semantics reports --------------------------------------------------


def build_semantics_markdown(sem: Semantics) -> str:
    lines: list[str] = []
    lines.append(f"# Semantic Labels — {sem.start_url or ''}")
    lines.append("")
    lines.append(f"*provider: {sem.provider} · engine {sem.engine_version} · "
                 f"schema {sem.schema_version}*")
    lines.append("")
    lines.append("## Label counts")
    lines.append("")
    counts = {k: v for k, v in sem.stats.items()
              if k not in ("total", "llm_refined")}
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append(f"Total {sem.stats.get('total', 0)} · "
                 f"LLM-refined {sem.stats.get('llm_refined', 0)}")
    lines.append("")
    lines.append("## Labelled controls (named)")
    lines.append("")
    for lab in sem.labels:
        if not lab.accessible_name:
            continue
        src = "🤖" if lab.source == "llm" else ""
        lines.append(f"- **{lab.accessible_name}** → `{lab.label}` "
                     f"({lab.confidence}{', ' + src if src else ''}) "
                     f"_{lab.rationale or ''}_")
    lines.append("")
    return "\n".join(lines)


def build_semantics_html(sem: Semantics) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    counts = {k: v for k, v in sem.stats.items()
              if k not in ("total", "llm_refined")}
    kpis = "".join(f"<span>{esc(k)}: <b>{esc(v)}</b></span>"
                   for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    rows = "".join(
        f"<tr><td>{esc(lab.accessible_name or '')}</td>"
        f"<td>{esc(lab.category)}</td><td>{esc(lab.landmark or '')}</td>"
        f"<td><b>{esc(lab.label)}</b></td><td>{esc(lab.confidence)}</td>"
        f"<td>{esc(lab.source)}</td><td>{esc(lab.rationale or '')}</td></tr>"
        for lab in sem.labels if lab.accessible_name
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Semantic Labels — {esc(sem.start_url or '')}</title>
<style>
  body {{ font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 980px; color:#1a1a1a; }}
  h1 {{ font-size:1.5rem; }} h2 {{ margin-top:2rem; font-size:1.15rem; }}
  table {{ border-collapse:collapse; width:100%; margin-top:.5rem; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid #eee; font-size:13px; }}
  th {{ background:#fafafa; }}
  .kpis span {{ display:inline-block; background:#f4f4f5; border-radius:6px; padding:6px 10px; margin:3px; font-size:13px; }}
  .meta {{ color:#666; font-size:13px; }}
</style></head><body>
<h1>Semantic Labels</h1>
<p class="meta">provider <b>{esc(sem.provider)}</b> · total {esc(sem.stats.get('total', 0))}
 · LLM-refined {esc(sem.stats.get('llm_refined', 0))} · schema {esc(sem.schema_version)}</p>
<div class="kpis">{kpis}</div>
<h2>Named controls</h2>
<table><thead><tr><th>Name</th><th>Category</th><th>Region</th><th>Label</th>
<th>Confidence</th><th>Source</th><th>Why</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


def write_semantics(sem: Semantics, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "semantics.json"),
        "markdown": str(out / "semantics.md"),
        "html": str(out / "semantics.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(sem.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path(paths["markdown"]).write_text(build_semantics_markdown(sem), encoding="utf-8")
    Path(paths["html"]).write_text(build_semantics_html(sem), encoding="utf-8")
    return paths


# --- V5.2: documentation ----------------------------------------------------


def build_documentation_markdown(doc: Documentation) -> str:
    lines: list[str] = []
    lines.append(f"# UI Documentation — {doc.start_url or ''}")
    lines.append("")
    src = " _(AI-drafted)_" if doc.overview_source == "llm" else ""
    lines.append(f"*generated {doc.generated_at} · provider {doc.provider} · "
                 f"crawl `{doc.source_crawl_id}`*")
    lines.append("")
    lines.append("## Overview" + src)
    lines.append("")
    lines.append(doc.overview)
    lines.append("")
    if doc.global_nav:
        lines.append("**Global navigation:** " + " › ".join(doc.global_nav))
        lines.append("")
    if doc.shared_components:
        lines.append("**Shared components:** " + ", ".join(doc.shared_components))
        lines.append("")
    lines.append("## Pages")
    lines.append("")
    for p in doc.pages:
        lines.append(f"### {p.title or p.url}")
        lines.append("")
        psrc = " _(AI-drafted)_" if p.purpose_source == "llm" else ""
        lines.append(f"{p.purpose}{psrc}")
        lines.append("")
        lines.append(f"- URL: `{p.url}` · depth {p.depth}")
        if p.regions:
            lines.append("- Regions: " + ", ".join(p.regions))
        if p.reached_from:
            lines.append("- Reached by: " + "; ".join(p.reached_from))
        if p.leads_to:
            lines.append("- Leads to: " + "; ".join(p.leads_to))
        for label, names in p.controls.items():
            lines.append(f"- {label}: " + ", ".join(f"“{n}”" for n in names[:12]))
        if p.screenshot:
            lines.append(f"- Screenshot: `screenshots/{p.screenshot}`")
        lines.append("")

        for form in p.forms:
            lines.append(f"**Form — {form.name}**")
            lines.append("")
            lines.append("| Field | Type | Required | Options | Default |")
            lines.append("| --- | --- | --- | --- | --- |")
            for f in form.fields:
                options = " / ".join(f.options[:10]) or "—"
                lines.append(f"| {f.label} | {f.ui_type} | "
                             f"{'yes' if f.required else 'no'} | {options} | "
                             f"{f.default or '—'} |")
            lines.append("")
        for table in p.tables:
            lines.append(f"**Table — {table.name}**: "
                         + (", ".join(table.columns) or "no declared columns")
                         + f" ({table.row_count} row(s) captured)"
                         + (f"; per-row actions: {', '.join(table.row_actions)}"
                            if table.row_actions else ""))
            lines.append("")
        for state in p.states:
            lines.append(f"**{state.kind.replace('-', ' ').title()} — "
                         f"{state.name or '(unnamed)'}**: opens when you click "
                         f"“{state.trigger_label}”"
                         + (f"; contains "
                            + ", ".join(f"“{c.accessible_name}”"
                                        for c in state.controls[:8]
                                        if c.accessible_name)
                            if state.controls else ""))
            lines.append("")
    return "\n".join(lines)


def build_documentation_html(doc: Documentation) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    def tag(src: str) -> str:
        return ' <em style="color:#8250df">(AI-drafted)</em>' if src == "llm" else ""

    sections = []
    for p in doc.pages:
        ctrls = "".join(
            f"<li><b>{esc(label)}:</b> " + ", ".join(esc(n) for n in names[:12]) + "</li>"
            for label, names in p.controls.items()
        )
        shot = (f'<div><img src="screenshots/{esc(p.screenshot)}" '
                f'style="max-width:320px;border:1px solid #ddd;border-radius:6px;margin-top:6px"></div>'
                if p.screenshot else "")
        nav = ""
        if p.reached_from:
            nav += (f"<p class='meta'><b>Reached by:</b> "
                    f"{esc('; '.join(p.reached_from))}</p>")
        if p.leads_to:
            nav += (f"<p class='meta'><b>Leads to:</b> "
                    f"{esc('; '.join(p.leads_to))}</p>")

        forms = ""
        for form in p.forms:
            rows = "".join(
                f"<tr><td>{esc(f.label)}</td><td>{esc(f.ui_type)}</td>"
                f"<td>{'yes' if f.required else 'no'}</td>"
                f"<td>{esc(' / '.join(f.options[:10]) or '—')}</td>"
                f"<td>{esc(f.default or '—')}</td></tr>"
                for f in form.fields
            )
            forms += (f"<h4>Form — {esc(form.name)}</h4>"
                      "<table><thead><tr><th>Field</th><th>Type</th>"
                      "<th>Required</th><th>Options</th><th>Default</th>"
                      f"</tr></thead><tbody>{rows}</tbody></table>")

        tables = "".join(
            f"<p><b>Table — {esc(t.name)}</b>: "
            f"{esc(', '.join(t.columns) or 'no declared columns')} "
            f"({esc(t.row_count)} row(s) captured)"
            + (f"; per-row actions: {esc(', '.join(t.row_actions))}"
               if t.row_actions else "") + "</p>"
            for t in p.tables
        )
        states = "".join(
            f"<p><b>{esc(st.kind.replace('-', ' ').title())} — "
            f"{esc(st.name or '(unnamed)')}</b>: opens when you click "
            f"“{esc(st.trigger_label)}”"
            + ("; contains " + esc(", ".join(
                c.accessible_name for c in st.controls[:8] if c.accessible_name))
               if st.controls else "") + "</p>"
            for st in p.states
        )

        sections.append(
            f"<section><h3>{esc(p.title or p.url)}</h3>"
            f"<p>{esc(p.purpose)}{tag(p.purpose_source)}</p>"
            f"<p class='meta'><code>{esc(p.url)}</code> · depth {esc(p.depth)}"
            + (f" · regions: {esc(', '.join(p.regions))}" if p.regions else "") + "</p>"
            f"{nav}<ul>{ctrls}</ul>{forms}{tables}{states}{shot}</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>UI Documentation — {esc(doc.start_url or '')}</title>
<style>
  body {{ font: 15px/1.6 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 900px; color:#1a1a1a; }}
  h1 {{ font-size:1.6rem; }} h2 {{ margin-top:2rem; }} h3 {{ margin-bottom:2px; }}
  code {{ background:#f4f4f5; padding:1px 4px; border-radius:3px; font-size:12px; }}
  section {{ border-top:1px solid #eee; padding-top:12px; margin-top:16px; }}
  ul {{ margin:6px 0; }} li {{ font-size:14px; }}
  h4 {{ margin:14px 0 4px; font-size:.9rem; color:#555;
       text-transform:uppercase; letter-spacing:.04em; }}
  table {{ border-collapse:collapse; width:100%; margin:.3rem 0 .8rem; }}
  th,td {{ text-align:left; padding:6px 9px; border-bottom:1px solid #eee;
          font-size:13px; vertical-align:top; }}
  th {{ background:#fafafa; }}
  .meta {{ color:#666; font-size:13px; }}
</style></head><body>
<h1>UI Documentation</h1>
<p class="meta">provider <b>{esc(doc.provider)}</b> · crawl <code>{esc(doc.source_crawl_id)}</code></p>
<h2>Overview{tag(doc.overview_source)}</h2>
<p>{esc(doc.overview)}</p>
{'<p><b>Global navigation:</b> ' + esc(' › '.join(doc.global_nav)) + '</p>' if doc.global_nav else ''}
{'<p><b>Shared components:</b> ' + esc(', '.join(doc.shared_components)) + '</p>' if doc.shared_components else ''}
<h2>Pages</h2>
{''.join(sections)}
</body></html>"""


def write_documentation(doc: Documentation, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "documentation.json"),
        "markdown": str(out / "documentation.md"),
        "html": str(out / "documentation.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(doc.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path(paths["markdown"]).write_text(build_documentation_markdown(doc), encoding="utf-8")
    Path(paths["html"]).write_text(build_documentation_html(doc), encoding="utf-8")
    return paths


# --- V5.3: QA plan ----------------------------------------------------------


def build_qaplan_markdown(plan: QAPlan) -> str:
    lines: list[str] = []
    lines.append(f"# QA Test Plan — {plan.start_url or ''}")
    lines.append("")
    src = " _(AI-drafted)_" if plan.strategy_source == "llm" else ""
    lines.append(f"*generated {plan.generated_at} · provider {plan.provider} · "
                 f"skeletons: {plan.language} · crawl `{plan.source_crawl_id}`*")
    lines.append("")
    lines.append("## Strategy" + src)
    lines.append("")
    lines.append(plan.strategy)
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for k, v in sorted(plan.stats.items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Runnable skeletons for automatable scenarios are in "
                 f"`generated_tests.{'spec.ts' if plan.language == 'ts' else 'py'}`. "
                 "Destructive controls are never automated.")
    lines.append("")
    lines.append("## Scenarios")
    lines.append("")
    for s in plan.scenarios:
        auto = "🤖 automatable" if s.automatable else "🚫 manual/guard"
        lines.append(f"### {s.title}")
        lines.append("")
        lines.append(f"- Type: {s.type} · priority {s.priority} · {auto}")
        lines.append(f"- Page: `{s.page_url}`")
        lines.append("- Steps:")
        for st in s.steps:
            bits = [st.action]
            if st.target:
                bits.append(f"“{st.target}”")
            if st.value:
                bits.append(f"= {st.value}")
            if st.note:
                bits.append(f"({st.note})")
            lines.append(f"  1. " + " ".join(bits))
        lines.append(f"- Expected: {s.expected}")
        if s.notes:
            lines.append(f"- Notes: {s.notes}")
        lines.append("")
    return "\n".join(lines)


def build_qaplan_html(plan: QAPlan) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    rows = []
    for s in plan.scenarios:
        steps = "<br>".join(
            esc(" ".join(filter(None, [st.action,
                                       f'“{st.target}”' if st.target else "",
                                       f"= {st.value}" if st.value else "",
                                       f"({st.note})" if st.note else ""])))
            for st in s.steps
        )
        auto = "✓" if s.automatable else "guard"
        rows.append(
            f"<tr><td>{esc(s.title)}</td><td>{esc(s.type)}</td>"
            f"<td>{esc(s.priority)}</td><td>{esc(auto)}</td>"
            f"<td>{steps}</td><td>{esc(s.expected)}</td></tr>"
        )
    kpis = "".join(f"<span>{esc(k)}: <b>{esc(v)}</b></span>"
                   for k, v in sorted(plan.stats.items()))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>QA Test Plan — {esc(plan.start_url or '')}</title>
<style>
  body {{ font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 1000px; color:#1a1a1a; }}
  h1 {{ font-size:1.5rem; }} h2 {{ margin-top:2rem; font-size:1.15rem; }}
  code {{ background:#f4f4f5; padding:1px 4px; border-radius:3px; font-size:12px; }}
  table {{ border-collapse:collapse; width:100%; margin-top:.5rem; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid #eee; font-size:13px; vertical-align:top; }}
  th {{ background:#fafafa; }}
  .kpis span {{ display:inline-block; background:#f4f4f5; border-radius:6px; padding:6px 10px; margin:3px; font-size:13px; }}
  .meta {{ color:#666; font-size:13px; }}
</style></head><body>
<h1>QA Test Plan</h1>
<p class="meta">provider <b>{esc(plan.provider)}</b> · skeletons {esc(plan.language)}
 · crawl <code>{esc(plan.source_crawl_id)}</code></p>
<h2>Strategy{' (AI-drafted)' if plan.strategy_source == 'llm' else ''}</h2>
<p>{esc(plan.strategy)}</p>
<div class="kpis">{kpis}</div>
<h2>Scenarios</h2>
<table><thead><tr><th>Title</th><th>Type</th><th>Pri</th><th>Auto</th>
<th>Steps</th><th>Expected</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""


def write_qaplan(plan: QAPlan, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "qa.json"),
        "markdown": str(out / "qa.md"),
        "html": str(out / "qa.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(plan.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path(paths["markdown"]).write_text(build_qaplan_markdown(plan), encoding="utf-8")
    Path(paths["html"]).write_text(build_qaplan_html(plan), encoding="utf-8")
    return paths


# --- C1: change diff reports ------------------------------------------------


_CHANGE_MARK = {"added": "+", "removed": "−", "renamed": "→", "changed": "~"}


def _diff_headline(diff: Diff) -> str:
    s = diff.stats
    if not s.get("total_changes"):
        return "**No changes detected** between these two snapshots."
    return (f"**{s['total_changes']} changes**: "
            f"{s['pages_added']} pages added, {s['pages_removed']} removed, "
            f"{s['pages_changed']} changed · "
            f"{s['elements_added']} elements added, {s['elements_removed']} removed, "
            f"**{s['elements_renamed']} renamed**")


def build_diff_markdown(diff: Diff) -> str:
    lines: list[str] = []
    lines.append(f"# UI Change Diff — {diff.new.start_url}")
    lines.append("")
    lines.append(f"*generated {diff.generated_at} · engine {diff.engine_version} · "
                 f"schema {diff.schema_version}*")
    lines.append("")
    lines.append(f"- Old: crawl `{diff.old.source_crawl_id}` "
                 f"({diff.old.analyzed_at}) — {diff.old.page_count} pages, "
                 f"{diff.old.element_count} elements")
    lines.append(f"- New: crawl `{diff.new.source_crawl_id}` "
                 f"({diff.new.analyzed_at}) — {diff.new.page_count} pages, "
                 f"{diff.new.element_count} elements")
    lines.append("")
    lines.append(_diff_headline(diff))
    lines.append("")

    if diff.narrative:
        src = (" _(AI-drafted from the findings below)_"
               if diff.narrative_source != "deterministic" else "")
        lines.append("## What changed" + src)
        lines.append("")
        lines.append(diff.narrative)
        lines.append("")

    if diff.stats.get("elements_renamed"):
        lines.append("## Renamed controls")
        lines.append("")
        lines.append("The same control carrying a different label — the signal "
                     "an add/remove pair would hide.")
        lines.append("")
        for c in diff.elements:
            if c.kind != "renamed":
                continue
            lines.append(f"- “{c.previous_name}” → **“{c.accessible_name}”** "
                         f"({c.category}{'/' + c.role if c.role else ''}) "
                         f"on `{c.page_url}` _(matched by {c.match})_")
        lines.append("")

    lines.append("## Pages")
    lines.append("")
    if not diff.pages:
        lines.append("_No page-level changes._")
    for p in diff.pages:
        mark = _CHANGE_MARK.get(p.kind, "?")
        title = p.title or "(untitled)"
        lines.append(f"- `{mark}` **{title}** — `{p.url}`")
        if p.previous_title:
            lines.append(f"  - Title: “{p.previous_title}” → “{p.title}”")
        if p.kind == "changed":
            lines.append(f"  - Elements: +{p.elements_added} "
                         f"−{p.elements_removed} →{p.elements_renamed} renamed")
    lines.append("")

    added_removed = [c for c in diff.elements if c.kind in ("added", "removed")]
    if added_removed:
        lines.append("## Elements added / removed")
        lines.append("")
        for c in added_removed:
            mark = _CHANGE_MARK[c.kind]
            name = c.accessible_name or "(unnamed)"
            lines.append(f"- `{mark}` “{name}” "
                         f"({c.category}{'/' + c.role if c.role else ''}) "
                         f"on `{c.page_url}`")
        lines.append("")

    if diff.components:
        lines.append("## Components")
        lines.append("")
        for c in diff.components:
            mark = _CHANGE_MARK[c.kind]
            label = c.label or "(unlabeled)"
            lines.append(f"- `{mark}` **{label}** ({c.component_kind}, "
                         f"{c.category}) — on {c.page_count} page(s)")
        lines.append("")

    return "\n".join(lines)


def build_diff_html(diff: Diff) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    s = diff.stats

    narrative_html = ""
    if diff.narrative:
        src = (" (AI-drafted from the findings below)"
               if diff.narrative_source != "deterministic" else "")
        body = "".join(f"<p>{esc(para)}</p>"
                       for para in diff.narrative.split("\n\n") if para.strip())
        narrative_html = (f"<h2>What changed{esc(src)}</h2>"
                          f"<div class='narrative'>{body}</div>")

    renamed_rows = "".join(
        f"<tr><td>“{esc(c.previous_name)}”</td>"
        f"<td><b>“{esc(c.accessible_name)}”</b></td>"
        f"<td>{esc(c.category)}{esc('/' + c.role if c.role else '')}</td>"
        f"<td><code>{esc(c.page_url)}</code></td>"
        f"<td class='meta'>{esc(c.match)}</td></tr>"
        for c in diff.elements if c.kind == "renamed"
    )
    renamed_html = (
        "<h2>Renamed controls</h2>"
        "<p class='meta'>The same control carrying a different label — the "
        "signal an add/remove pair would hide.</p>"
        "<table><thead><tr><th>Was</th><th>Now</th><th>Kind</th><th>Page</th>"
        "<th>Matched by</th></tr></thead>"
        f"<tbody>{renamed_rows}</tbody></table>"
    ) if renamed_rows else ""

    page_rows = "".join(
        f"<tr><td class='mark {esc(p.kind)}'>{esc(_CHANGE_MARK.get(p.kind, '?'))}</td>"
        f"<td>{esc(p.title or '(untitled)')}<br><code>{esc(p.url)}</code></td>"
        f"<td>{esc(p.kind)}</td>"
        f"<td>+{esc(p.elements_added)} −{esc(p.elements_removed)} "
        f"→{esc(p.elements_renamed)}</td></tr>"
        for p in diff.pages
    )

    element_rows = "".join(
        f"<tr><td class='mark {esc(c.kind)}'>{esc(_CHANGE_MARK[c.kind])}</td>"
        f"<td>{esc(c.accessible_name or '(unnamed)')}</td>"
        f"<td>{esc(c.category)}{esc('/' + c.role if c.role else '')}</td>"
        f"<td><code>{esc(c.page_url)}</code></td></tr>"
        for c in diff.elements if c.kind in ("added", "removed")
    )
    elements_html = (
        "<h2>Elements added / removed</h2><table>"
        "<thead><tr><th></th><th>Control</th><th>Kind</th><th>Page</th></tr></thead>"
        f"<tbody>{element_rows}</tbody></table>"
    ) if element_rows else ""

    component_rows = "".join(
        f"<tr><td class='mark {esc(c.kind)}'>{esc(_CHANGE_MARK[c.kind])}</td>"
        f"<td>{esc(c.label or '(unlabeled)')}</td>"
        f"<td>{esc(c.component_kind)}</td><td>{esc(c.category)}</td>"
        f"<td>{esc(c.page_count)}</td></tr>"
        for c in diff.components
    )
    components_html = (
        "<h2>Components</h2><table>"
        "<thead><tr><th></th><th>Label</th><th>Kind</th><th>Category</th>"
        "<th>Pages</th></tr></thead>"
        f"<tbody>{component_rows}</tbody></table>"
    ) if component_rows else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>UI Change Diff — {esc(diff.new.start_url)}</title>
<style>
  body {{ font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 960px; color: #1a1a1a; }}
  h1 {{ font-size: 1.5rem; }} h2 {{ margin-top: 2rem; font-size: 1.15rem; }}
  code {{ background: #f4f4f5; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee;
           vertical-align: top; font-size: 13px; }}
  th {{ background: #fafafa; }}
  .meta {{ color: #666; font-size: 13px; }}
  .kpis span {{ display:inline-block; background:#f4f4f5; border-radius:6px;
               padding:6px 10px; margin:3px; }}
  .mark {{ font-weight: 700; width: 1.5rem; text-align: center; }}
  .mark.added {{ color: #15803d; }}
  .mark.removed {{ color: #b91c1c; }}
  .mark.renamed, .mark.changed {{ color: #b45309; }}
  .narrative p {{ margin: .5rem 0; }}
</style></head><body>
<h1>UI Change Diff</h1>
<p class="meta">Site <code>{esc(diff.new.start_url)}</code> · generated
 {esc(diff.generated_at)} · engine {esc(diff.engine_version)}</p>
<p class="meta">
  Old: crawl <code>{esc(diff.old.source_crawl_id)}</code>
  ({esc(diff.old.page_count)} pages, {esc(diff.old.element_count)} elements)<br>
  New: crawl <code>{esc(diff.new.source_crawl_id)}</code>
  ({esc(diff.new.page_count)} pages, {esc(diff.new.element_count)} elements)
</p>
{narrative_html}
<div class="kpis">
  <span>Pages: <b>+{esc(s['pages_added'])}</b> / <b>−{esc(s['pages_removed'])}</b>
   / <b>~{esc(s['pages_changed'])}</b></span>
  <span>Elements: <b>+{esc(s['elements_added'])}</b> /
   <b>−{esc(s['elements_removed'])}</b></span>
  <span>Renamed: <b>{esc(s['elements_renamed'])}</b></span>
  <span>Components: <b>+{esc(s['components_added'])}</b> /
   <b>−{esc(s['components_removed'])}</b></span>
</div>
{renamed_html}
<h2>Pages ({esc(len(diff.pages))})</h2>
<table><thead><tr><th></th><th>Page</th><th>Change</th><th>Elements</th></tr></thead>
<tbody>{page_rows}</tbody></table>
{elements_html}
{components_html}
</body></html>"""


def write_diff(diff: Diff, output_dir: str) -> dict[str, str]:
    """Write diff.json + diff.md + diff.html into `output_dir`."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "diff.json"),
        "markdown": str(out / "diff.md"),
        "html": str(out / "diff.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(diff.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path(paths["markdown"]).write_text(build_diff_markdown(diff), encoding="utf-8")
    Path(paths["html"]).write_text(build_diff_html(diff), encoding="utf-8")
    return paths


# --- Relationships ----------------------------------------------------------


def write_relations(relations: Relations, output_dir: str) -> dict[str, str]:
    """Write `relations.json` into `output_dir`.

    Written on every run, like the rest of the inventory: a file recording
    "0 relationships, because nothing on this site links to anything" is a
    finding. A missing file is just ambiguous.

    JSON only — the readable rendering of these relationships is the crawl
    report itself, which is where a person actually looks.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "relations.json"
    path.write_text(
        json.dumps(relations.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"json": str(path)}


def write_reports(crawl: Crawl, output_dir: str,
                  relations: Relations | None = None) -> dict[str, str]:
    """Write crawl.json + report.md + report.html into `output_dir`."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "crawl.json"),
        "markdown": str(out / "report.md"),
        "html": str(out / "report.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(crawl.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    relations = relations if relations is not None else build_relations(crawl)
    paths.update(write_relations(relations, output_dir))
    Path(paths["markdown"]).write_text(
        build_markdown(crawl, relations), encoding="utf-8")
    Path(paths["html"]).write_text(build_html(crawl, relations), encoding="utf-8")
    return paths
