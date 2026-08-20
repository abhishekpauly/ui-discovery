"""Run inventory — the plain-facts artifacts every crawl must leave behind.

`crawl.json` is the canonical model, but it is a large nested document. These
files answer the questions people actually ask of a capture, each in a form
you can open, grep, diff or paste into a ticket without a JSON viewer:

    urls.txt        every screen that was captured, one per line
    endpoints.md    the API surface observed behind the UI
    elements.csv    every UI element found, one row per element per screen
    controls.csv    every clickable, with its label, options and destination
    summary.md      screen count, per-screen element counts, totals
    inventory.json  all of the above as data

Written on **every** run, including when a stage is skipped or a capability
is off — a file that says "0 endpoints, because the probe did not run" is
worth having; a missing file is just ambiguous.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .models import Crawl
from .taxonomy import CATALOGUE, coverage

ELEMENT_COLUMNS = (
    "page_url", "page_title", "depth", "category", "ui_type", "role",
    "accessible_name", "text", "visible", "enabled", "landmark",
    "shadow_depth", "frame", "dom_path",
    # What the control offers and what state it is in — the difference between
    # "a dropdown" and "the Status dropdown, currently In progress, offering
    # Open / In progress / Closed".
    "options", "option_count", "states", "value", "help_text", "group",
    "required", "parent_path", "owner_form", "controls", "columns",
    "row_count", "clip_screenshot",
)

# One row per thing a person can click, which is the question the feedback
# actually asked: what are all the clickable elements, what are they called,
# and what do they offer?
CONTROL_COLUMNS = (
    "page_url", "page_title", "label", "ui_type", "category", "region",
    "enabled", "options", "option_count", "leads_to", "dom_path",
)

# Categories that represent something a person clicks rather than structure.
CLICKABLE_CATEGORIES = ("button", "link", "tab", "menu", "disclosure")


def _endpoints(crawl: Crawl) -> list[dict[str, Any]]:
    """Observed API endpoints, aggregated across pages. Empty unless the crawl
    ran with `--probe`, which is what observes network traffic."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for node in crawl.pages:
        if not node.probe:
            continue
        for req in node.probe.network:
            if not req.is_api:
                continue
            key = (req.method.upper(), req.endpoint_pattern or req.url)
            entry = seen.setdefault(key, {
                "method": key[0],
                "endpoint": key[1],
                "calls": 0,
                "statuses": [],
                "graphql": req.is_graphql,
                "seen_on": [],
            })
            entry["calls"] += 1
            if req.status and req.status not in entry["statuses"]:
                entry["statuses"].append(req.status)
            if node.url not in entry["seen_on"]:
                entry["seen_on"].append(node.url)
    return sorted(seen.values(), key=lambda e: (-e["calls"], e["endpoint"]))


def _screens(crawl: Crawl) -> list[dict[str, Any]]:
    screens = []
    for node in crawl.pages:
        page = node.page
        counts = {k: v for k, v in page.counts.items()
                  if k not in ("visible_elements", "total_elements", "headings")}
        screens.append({
            "url": node.url,
            "title": page.title,
            "depth": node.depth,
            "http_status": page.readiness.get("http_status"),
            "elements_total": page.counts.get("total_elements", 0),
            "elements_visible": page.counts.get("visible_elements", 0),
            "headings": page.counts.get("headings", 0),
            "by_category": counts,
            "screenshot": (Path(page.screenshot_path).name
                           if page.screenshot_path else None),
            "out_links": len(node.out_links),
            "probed": node.probe is not None,
        })
    return screens


def _not_captured(crawl: Crawl) -> list[str]:
    """URLs the crawl *found* but never visited — almost always the page
    budget running out. Silently truncating a capture and reporting success
    is the kind of thing that makes someone trust an incomplete inventory."""
    captured = {n.url for n in crawl.pages}
    discovered = {edge["to"] for edge in crawl.navigation}
    return sorted(discovered - captured)


def _ui_types(crawl: Crawl) -> dict[str, int]:
    counter: Counter = Counter()
    for node in crawl.pages:
        for el in node.page.elements:
            if el.ui_type:
                counter[el.ui_type] += 1
    return dict(counter)


def build_inventory(crawl: Crawl) -> dict[str, Any]:
    """The whole inventory as plain data. Pure — no filesystem access."""
    screens = _screens(crawl)
    endpoints = _endpoints(crawl)
    totals: Counter = Counter()
    for node in crawl.pages:
        for key, value in node.page.counts.items():
            if key not in ("visible_elements", "total_elements"):
                totals[key] += value
    missed = _not_captured(crawl)
    return {
        "target": crawl.config.start_url,
        "crawl_id": crawl.crawl_id,
        "captured_at": crawl.finished_at,
        "engine_version": crawl.engine_version,
        "screens_count": len(screens),
        "elements_count": sum(s["elements_total"] for s in screens),
        "endpoints_count": len(endpoints),
        "probe_ran": any(s["probed"] for s in screens),
        "totals_by_category": dict(totals.most_common()),
        "screens": screens,
        "endpoints": endpoints,
        "discovered_not_captured": missed,
        "budget_exhausted": bool(missed),
        "ui_types": dict(sorted(_ui_types(crawl).items(), key=lambda kv: -kv[1])),
        "ui_coverage": coverage(_ui_types(crawl)),
        "unmarked_clickables": crawl.config.unmarked_clickables,
        "deep_nav": crawl.config.deep_nav,
    }


def _options_cell(el) -> str:
    """A control's choices as one readable cell, marking the current one."""
    return " | ".join(
        (f"*{o.label}*" if o.selected else o.label) for o in el.options if o.label
    )


def _states_cell(el) -> str:
    return "; ".join(f"{k}={v}" for k, v in sorted(el.states.items()))


def _elements_csv(crawl: Crawl) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(ELEMENT_COLUMNS)
    for node in crawl.pages:
        for el in node.page.elements:
            writer.writerow([
                node.url, node.page.title, node.depth, el.category,
                el.ui_type or "", el.role or "", el.accessible_name or "",
                (el.text or "")[:120], el.visible, el.enabled,
                el.landmark or "", el.shadow_depth, el.frame or "",
                el.dom_path,
                _options_cell(el), el.option_count, _states_cell(el),
                el.value or "", el.described_by or "", el.group or "",
                el.states.get("required") == "true",
                el.parent_path, el.owner_form or "",
                " | ".join(el.controls), " | ".join(el.columns),
                el.row_count, el.clip_screenshot or "",
            ])
    return buf.getvalue()


def _controls_csv(crawl: Crawl) -> str:
    """Every clickable element, with the label a person reads and where it goes.

    `leads_to` is filled from the crawl's own navigation edges, so a row says
    not just "there is a link called Orders" but "clicking Orders takes you to
    /orders.html" — the two facts a reader needs to follow the product.
    """
    destinations: dict[tuple[str, str], str] = {}
    for edge in crawl.navigation:
        label = (edge.get("label") or "").strip()
        if not label:
            continue
        destinations.setdefault((edge.get("from", ""), label), edge.get("to", ""))

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CONTROL_COLUMNS)
    for node in crawl.pages:
        for el in node.page.elements:
            if el.category not in CLICKABLE_CATEGORIES:
                continue
            label = (el.accessible_name or el.text or "").strip()[:120]
            writer.writerow([
                node.url, node.page.title, label,
                el.ui_type or "", el.category, el.landmark or "",
                el.enabled, _options_cell(el), el.option_count,
                destinations.get((node.url, label), ""),
                el.dom_path,
            ])
    return buf.getvalue()


def _endpoints_markdown(inv: dict[str, Any]) -> str:
    lines = [f"# API endpoints — {inv['target']}", "",
             f"*crawl `{inv['crawl_id']}` · {inv['captured_at']}*", ""]
    if not inv["probe_ran"]:
        lines += [
            "_No endpoints recorded: this crawl ran without `--probe`, which "
            "is what observes network traffic. Re-run with `--probe` to "
            "capture the API surface._", "",
        ]
        return "\n".join(lines)
    if not inv["endpoints"]:
        lines += ["_The probe ran but observed no API calls._", ""]
        return "\n".join(lines)

    lines += [f"**{inv['endpoints_count']} endpoints** observed behind the UI.",
              "", "| Method | Endpoint | Calls | Statuses | Seen on |",
              "| --- | --- | --- | --- | --- |"]
    for e in inv["endpoints"]:
        statuses = ", ".join(str(s) for s in e["statuses"]) or "—"
        pages = len(e["seen_on"])
        lines.append(f"| {e['method']} | `{e['endpoint']}` | {e['calls']} | "
                     f"{statuses} | {pages} screen(s) |")
    lines.append("")
    lines.append("_Method, URL and status only — never headers or bodies, and "
                 "sensitive query values are redacted._")
    return "\n".join(lines)


def _summary_markdown(inv: dict[str, Any]) -> str:
    lines = [
        f"# Capture summary — {inv['target']}", "",
        f"*crawl `{inv['crawl_id']}` · {inv['captured_at']} · "
        f"engine {inv['engine_version']}*", "",
        f"- **Screens captured: {inv['screens_count']}**",
        f"- **UI elements found: {inv['elements_count']}**",
        f"- **API endpoints observed: {inv['endpoints_count']}**"
        + ("" if inv["probe_ran"] else "  _(probe not run — use `--probe`)_"),
        "",
        "## Elements by kind (all screens)", "",
    ]
    if inv["unmarked_clickables"] and not inv["deep_nav"]:
        lines[6:6] = [
            f"> ℹ️ **There may be more screens.** {inv['unmarked_clickables']} "
            f"element(s) are clickable but were never marked up as links "
            f"(no anchor, no button, no ARIA role), so link-following cannot "
            f"see where they go. Re-run with `--deep-nav` to click them, or "
            f"seed those areas with `modules:`.",
            "",
        ]
    if inv["discovered_not_captured"]:
        missed = inv["discovered_not_captured"]
        lines[6:6] = [
            f"> ⚠️ **This capture is incomplete.** {len(missed)} screen(s) "
            f"were discovered but not visited — the page budget ran out. "
            f"Raise `--max-pages` and re-run. They are listed at the "
            f"bottom of this file.",
            "",
        ]
    for kind, count in inv["totals_by_category"].items():
        lines.append(f"- {kind}: {count}")
    cov = inv["ui_coverage"]
    lines += ["", "## UI types found", "",
              f"**{cov['found_count']} of {cov['catalogue_size']}** recognised "
              f"UI types are present on this app.", ""]
    for group, members in CATALOGUE.items():
        present = [(t, cov["found"][t]) for t in members if t in cov["found"]]
        if not present:
            continue
        lines.append(f"- **{group}** — "
                     + ", ".join(f"{t} ({n})" for t, n in present))
    if cov["app_declared"]:
        lines += ["",
                  "Widget names the app declares for itself "
                  "(`aria-roledescription`): "
                  + ", ".join(f"{t} ({n})" for t, n in cov["app_declared"].items())]
    lines += ["",
              f"_{len(cov['absent'])} recognised types are absent from this "
              f"app. A further {len(cov['not_detectable'])} types "
              f"(cards, widgets, tags, icon meaning, …) are **not "
              f"deterministically detectable** — they have no standard markup, "
              f"so their absence here says nothing about your product. See "
              f"`inventory.json` for the full breakdown._",
              ""]
    lines += ["", "## Screens", "",
              "| # | Screen | Elements | Visible | Links | Screenshot |",
              "| --- | --- | --- | --- | --- | --- |"]
    for i, s in enumerate(inv["screens"], 1):
        shot = f"`screenshots/{s['screenshot']}`" if s["screenshot"] else "—"
        title = s["title"] or "(untitled)"
        lines.append(f"| {i} | **{title}**<br>`{s['url']}` | "
                     f"{s['elements_total']} | {s['elements_visible']} | "
                     f"{s['out_links']} | {shot} |")
    if inv["discovered_not_captured"]:
        lines += ["", "## Discovered but not captured", "",
                  "_Found via links, never visited — the page budget ran out._", ""]
        lines += [f"- `{u}`" for u in inv["discovered_not_captured"][:100]]

    lines += ["", "## Files in this folder", "",
              "| File | What it is |", "| --- | --- |",
              "| `urls.txt` | Every captured screen, one URL per line |",
              "| `elements.csv` | Every UI element, one row per element |",
              "| `controls.csv` | Every clickable, its label, options and destination |",
              "| `relations.json` | How screens and elements connect |",
              "| `endpoints.md` | API surface observed behind the UI |",
              "| `screenshots/` | One full-page screenshot per screen |",
              "| `screenshots/components/` | Forms, dialogs, tab panels and tables, cropped |",
              "| `screenshots/states/` | Modals, menus and panels revealed by clicking |",
              "| `crawl.json` | The canonical model everything else derives from |",
              "| `report.html` | The readable crawl report |", ""]
    return "\n".join(lines)


def write_inventory(crawl: Crawl, output_dir: str) -> dict[str, str]:
    """Write every run artifact into `output_dir`. Always writes all of them."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    inv = build_inventory(crawl)

    paths = {
        "inventory": str(out / "inventory.json"),
        "urls": str(out / "urls.txt"),
        "elements": str(out / "elements.csv"),
        "controls": str(out / "controls.csv"),
        "endpoints": str(out / "endpoints.md"),
        "summary": str(out / "summary.md"),
    }
    Path(paths["inventory"]).write_text(
        json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(paths["urls"]).write_text(
        "\n".join(s["url"] for s in inv["screens"]) + "\n", encoding="utf-8")
    Path(paths["elements"]).write_text(_elements_csv(crawl), encoding="utf-8")
    Path(paths["controls"]).write_text(_controls_csv(crawl), encoding="utf-8")
    Path(paths["endpoints"]).write_text(
        _endpoints_markdown(inv), encoding="utf-8")
    Path(paths["summary"]).write_text(
        _summary_markdown(inv), encoding="utf-8")
    return paths


# --- module-wise layout ------------------------------------------------------

GENERAL_FOLDER = "general"


def _folder_name(label: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (label or "").strip()).strip("-._")
    return cleaned[:60] or fallback


def assign_modules(
    crawl: Crawl, modules: Sequence[tuple[str, str]],
) -> dict[str, list]:
    """Group captured pages by module, by URL path.

    `modules` is (name, start_url) pairs. A page belongs to the module whose
    start path is the longest prefix of its own — longest wins, so a module at
    `/platform/rag/containers` beats one at `/platform`. Anything matching no
    module lands in `general`, which is most pages on most sites and is not a
    failure.
    """
    prefixes = []
    for name, start_url in modules:
        path = (urlparse(start_url).path or "/").rstrip("/")
        if path:
            prefixes.append((path, _folder_name(name, path.strip("/") or "module")))

    grouped: dict[str, list] = {}
    for node in crawl.pages:
        page_path = (urlparse(node.url).path or "/").rstrip("/")
        best = ""
        folder = GENERAL_FOLDER
        for prefix, name in prefixes:
            if (page_path == prefix or page_path.startswith(prefix + "/")) \
                    and len(prefix) > len(best):
                best, folder = prefix, name
        grouped.setdefault(folder, []).append(node)
    return grouped


def write_module_artifacts(
    crawl: Crawl, product_dir: str, modules: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """Write a per-module folder of artifacts under the product folder.

    Each module folder is a self-contained view of its own screens — the same
    files as the product-level capture, scoped to that module, with copies of
    just those screenshots. Self-contained on purpose: a module folder is the
    thing you hand to the team that owns that module.

    The whole-crawl artifacts stay at the product level; `crawl.json` is never
    split, because it is the canonical model and a partial one would be a
    different, lesser artifact wearing the same name.
    """
    root = Path(product_dir)
    written: dict[str, str] = {}

    for folder, nodes in sorted(assign_modules(crawl, modules).items()):
        if not nodes:
            continue
        sub = root / folder
        sub.mkdir(parents=True, exist_ok=True)

        # A Crawl carrying only this module's pages, so every existing
        # renderer works on it unchanged.
        scoped = crawl.model_copy(update={"pages": nodes})
        write_inventory(scoped, str(sub))

        shots = sub / "screenshots"
        for node in nodes:
            src = node.page.screenshot_path
            if not src or not Path(src).exists():
                continue
            shots.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, shots / Path(src).name)
        written[folder] = str(sub)

    return written
