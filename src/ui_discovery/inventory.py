"""Run inventory — the plain-facts artifacts every crawl must leave behind.

`crawl.json` is the canonical model, but it is a large nested document. These
files answer the questions people actually ask of a capture, each in a form
you can open, grep, diff or paste into a ticket without a JSON viewer:

    urls.txt        every screen that was captured, one per line
    endpoints.md    the API surface observed behind the UI
    elements.csv    every UI element found, one row per element per screen
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
from collections import Counter
from pathlib import Path
from typing import Any

from .models import Crawl

ELEMENT_COLUMNS = (
    "page_url", "page_title", "depth", "category", "role",
    "accessible_name", "text", "visible", "enabled", "landmark",
    "shadow_depth", "frame", "dom_path",
)


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
        "unmarked_clickables": crawl.config.unmarked_clickables,
        "deep_nav": crawl.config.deep_nav,
    }


def _elements_csv(crawl: Crawl) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(ELEMENT_COLUMNS)
    for node in crawl.pages:
        for el in node.page.elements:
            writer.writerow([
                node.url, node.page.title, node.depth, el.category,
                el.role or "", el.accessible_name or "",
                (el.text or "")[:120], el.visible, el.enabled,
                el.landmark or "", el.shadow_depth, el.frame or "",
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
              "| `endpoints.md` | API surface observed behind the UI |",
              "| `screenshots/` | One full-page screenshot per screen |",
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
        "endpoints": str(out / "endpoints.md"),
        "summary": str(out / "summary.md"),
    }
    Path(paths["inventory"]).write_text(
        json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(paths["urls"]).write_text(
        "\n".join(s["url"] for s in inv["screens"]) + "\n", encoding="utf-8")
    Path(paths["elements"]).write_text(_elements_csv(crawl), encoding="utf-8")
    Path(paths["endpoints"]).write_text(
        _endpoints_markdown(inv), encoding="utf-8")
    Path(paths["summary"]).write_text(
        _summary_markdown(inv), encoding="utf-8")
    return paths
