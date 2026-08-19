"""Render human-readable reports FROM the structured `Crawl` model.

Reports are a presentation layer. The JSON model is the source of truth; these
renderers never hold information that isn't in it.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

from .models import (
    Analysis,
    Crawl,
    Documentation,
    Diff,
    InteractionProbe,
    PageNode,
    QAPlan,
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


# --- Markdown ---------------------------------------------------------------


def build_markdown(crawl: Crawl) -> str:
    c = crawl.config
    s = crawl.stats
    lines: list[str] = []
    lines.append(f"# UI Crawl Report — {c.start_url}")
    lines.append("")
    lines.append(f"*Crawl `{crawl.crawl_id}` · engine {crawl.engine_version} · "
                 f"schema {crawl.schema_version}*")
    lines.append("")

    lines.append("## Crawl summary")
    lines.append("")
    lines.append(f"- Start URL: `{c.start_url}`")
    lines.append(f"- Strategy: {c.strategy} · max depth {c.max_depth} · "
                 f"max pages {c.max_pages}")
    lines.append(f"- Started: {crawl.started_at}")
    lines.append(f"- Finished: {crawl.finished_at}")
    lines.append(f"- Runtime: {s.runtime_seconds}s")
    lines.append(f"- Pages crawled: **{s.pages_crawled}** · failed: {s.pages_failed}")
    lines.append(f"- Unique URLs seen: {s.unique_urls} · "
                 f"navigation edges: {s.links_discovered}")
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

    lines.append("## Page graph")
    lines.append("")
    lines.append("Pages by depth from the start URL:")
    lines.append("")
    for node in crawl.pages:
        indent = "  " * (node.depth or 0)
        n_el = node.page.counts.get("total_elements", 0)
        lines.append(f"{indent}- {_page_label(node)}  "
                     f"_(depth {node.depth}, {n_el} elements)_")
    lines.append("")

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
        lines.append(f"- Network requests: {totals['network_requests']} "
                     f"(API: {totals['api_requests']})")
        lines.append("")
        endpoints = _api_endpoints(crawl)
        if endpoints:
            lines.append("Observed API endpoints:")
            lines.append("")
            for pattern, count in endpoints:
                lines.append(f"- `{pattern}` ({count})")
            lines.append("")

    lines.append("## Page inventory")
    lines.append("")
    for node in crawl.pages:
        p = node.page
        lines.append(f"### {p.title or '(untitled)'}")
        lines.append("")
        lines.append(f"- URL: `{node.url}`")
        lines.append(f"- Depth: {node.depth}")
        lines.append(f"- HTTP status: {p.readiness.get('http_status')} · "
                     f"networkidle: {p.readiness.get('networkidle')}")
        counts = {k: v for k, v in p.counts.items()
                  if k not in ("visible_elements", "total_elements")}
        lines.append("- Elements: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        headings = ", ".join(h.text for h in p.headings) or "—"
        lines.append(f"- Headings: {headings}")
        named = [e.accessible_name for e in p.elements
                 if e.category in ("button", "link") and e.accessible_name]
        if named:
            lines.append("- Named controls: " + ", ".join(f"“{n}”" for n in named[:12]))
        if node.out_links:
            lines.append(f"- Out-links ({len(node.out_links)}): "
                         + ", ".join(f"`{u.rsplit('/', 1)[-1] or u}`"
                                     for u in node.out_links[:10]))
        if node.probe:
            ps = node.probe.stats
            lines.append(f"- Probe: {ps.get('executed', 0)} executed · "
                         f"{ps.get('blocked', 0)} blocked · "
                         f"{ps.get('state_changing', 0)} state-changing · "
                         f"{ps.get('network_requests', 0)} requests")
        lines.append("")

    return "\n".join(lines)


# --- HTML -------------------------------------------------------------------


def build_html(crawl: Crawl) -> str:
    c, s = crawl.config, crawl.stats

    def esc(x: object) -> str:
        return html.escape(str(x))

    rows = []
    for node in crawl.pages:
        p = node.page
        counts = {k: v for k, v in p.counts.items()
                  if k not in ("visible_elements", "total_elements")}
        counts_str = ", ".join(f"{k}&nbsp;{v}" for k, v in counts.items())
        shot = p.screenshot_path
        shot_rel = ("screenshots/" + Path(shot).name) if shot else ""
        thumb = (f'<a href="{esc(shot_rel)}"><img src="{esc(shot_rel)}" '
                 f'style="height:60px;border:1px solid #ddd;border-radius:4px"></a>'
                 if shot else "")
        rows.append(
            "<tr>"
            f"<td>{esc(node.depth)}</td>"
            f"<td>{esc(p.title or '(untitled)')}<br><code>{esc(node.url)}</code></td>"
            f"<td>{esc(p.readiness.get('http_status'))}</td>"
            f"<td>{counts_str}</td>"
            f"<td>{thumb}</td>"
            "</tr>"
        )

    totals = _ui_totals(crawl)
    totals_str = " · ".join(f"{esc(k)}: <b>{esc(v)}</b>"
                            for k, v in sorted(totals.items(), key=lambda kv: -kv[1]))

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

    # H2: probe sections appear only when the crawl actually probed.
    probe_html = ""
    pt = _probe_totals(crawl)
    if pt:
        endpoints = _api_endpoints(crawl)
        endpoints_html = ""
        if endpoints:
            items = "".join(
                f"<tr><td><code>{esc(pattern)}</code></td><td>{esc(count)}</td></tr>"
                for pattern, count in endpoints
            )
            endpoints_html = (
                "<h2>Observed API endpoints</h2><table>"
                "<thead><tr><th>Endpoint pattern</th><th>Requests</th></tr></thead>"
                f"<tbody>{items}</tbody></table>"
            )
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
{endpoints_html}"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>UI Crawl Report — {esc(c.start_url)}</title>
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
  .banner {{ padding: 10px 12px; border-radius: 6px; border-left: 4px solid; }}
  .banner.error {{ background:#fef2f2; border-color:#b91c1c; color:#7f1d1d; }}
  .banner.note {{ background:#f4f4f5; border-color:#9ca3af; color:#374151; }}
</style></head><body>
<h1>UI Crawl Report</h1>
<p class="meta">Start <code>{esc(c.start_url)}</code> · crawl <code>{esc(crawl.crawl_id)}</code>
 · engine {esc(crawl.engine_version)} · schema {esc(crawl.schema_version)}</p>
{auth_html}
<div class="kpis">
  <span>Pages crawled: <b>{esc(s.pages_crawled)}</b></span>
  <span>Failed: <b>{esc(s.pages_failed)}</b></span>
  <span>Nav edges: <b>{esc(s.links_discovered)}</b></span>
  <span>Unique URLs: <b>{esc(s.unique_urls)}</b></span>
  <span>Runtime: <b>{esc(s.runtime_seconds)}s</b></span>
  <span>Strategy: <b>{esc(c.strategy)}</b> (depth≤{esc(c.max_depth)}, ≤{esc(c.max_pages)} pages)</span>
</div>
<h2>UI inventory (all pages)</h2>
<p>{totals_str}</p>
{probe_html}
<h2>Pages ({esc(s.pages_crawled)})</h2>
<table>
<thead><tr><th>Depth</th><th>Page</th><th>Status</th><th>Elements</th><th>Screenshot</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
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
        for label, names in p.controls.items():
            lines.append(f"- {label}: " + ", ".join(f"“{n}”" for n in names[:12]))
        if p.links:
            lines.append("- Links to: " + ", ".join(f"`{u}`" for u in p.links[:10]))
        if p.screenshot:
            lines.append(f"- Screenshot: `screenshots/{p.screenshot}`")
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
        sections.append(
            f"<section><h3>{esc(p.title or p.url)}</h3>"
            f"<p>{esc(p.purpose)}{tag(p.purpose_source)}</p>"
            f"<p class='meta'><code>{esc(p.url)}</code> · depth {esc(p.depth)}"
            + (f" · regions: {esc(', '.join(p.regions))}" if p.regions else "") + "</p>"
            f"<ul>{ctrls}</ul>{shot}</section>"
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


def write_reports(crawl: Crawl, output_dir: str) -> dict[str, str]:
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
    Path(paths["markdown"]).write_text(build_markdown(crawl), encoding="utf-8")
    Path(paths["html"]).write_text(build_html(crawl), encoding="utf-8")
    return paths
