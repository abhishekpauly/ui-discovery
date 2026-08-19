"""V4 reports — the source index and the correlation, rendered.

Kept beside the code that produces them rather than in the top-level
`reports.py`, which is already long.

The rendering has one job beyond legibility: never let a reader mistake a
heuristic for a fact. Confidence is a column, not a footnote, and the
alternatives behind an ambiguous match are shown rather than hidden.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from ..models import CorrelationReport, SourceIndex

CONFIDENCE_ORDER = ("confirmed", "high", "medium", "low", "unknown")
CONFIDENCE_MARK = {
    "confirmed": "✅", "high": "🟢", "medium": "🟡", "low": "🟠", "unknown": "⚪",
}

_DISCLAIMER = (
    "Every link below is a **heuristic match between two approximations** — a "
    "static source scan and a runtime capture. Confidence and evidence are "
    "shown for each; an ambiguous match is reported as `low` with its "
    "alternatives rather than resolved to a guess."
)

_SECTIONS = (("element", "Controls"), ("route", "Routes"),
             ("endpoint", "API endpoints"))


def write_source_index(index: SourceIndex, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": str(out / "source_index.json")}
    Path(paths["json"]).write_text(
        json.dumps(index.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return paths


def build_markdown(report: CorrelationReport) -> str:
    s = report.stats
    lines: list[str] = [
        "# Runtime → Source Correlation",
        "",
        f"*generated {report.generated_at} · repo `{report.repo_path}` · "
        f"crawl `{report.source_crawl_id}` · engine {report.engine_version}*",
        "",
        f"> {_DISCLAIMER}",
        "",
        f"- Correlations: **{s['correlations']}** (elements {s['elements']}, "
        f"routes {s['routes']}, endpoints {s['endpoints']})",
        "- Confidence: " + " · ".join(
            f"{CONFIDENCE_MARK[level]} {level} "
            f"{s.get('confidence_' + level, 0)}" for level in CONFIDENCE_ORDER),
        f"- Unmatched: {s['unmatched_runtime']} runtime, "
        f"{s['unmatched_source']} source",
        "",
    ]

    for kind, title in _SECTIONS:
        rows = [c for c in report.correlations if c.kind == kind]
        if not rows:
            continue
        lines += [f"## {title}", "", "| Confidence | Observed | Source | Evidence |",
                  "| --- | --- | --- | --- |"]
        for c in sorted(rows, key=lambda r: CONFIDENCE_ORDER.index(r.confidence)):
            where = f"`{c.ref.path}:{c.ref.line}`" if c.ref else "—"
            name = f"**{c.source_name}**<br>{where}" if c.source_name else where
            alt = (f"<br>_alternatives: {', '.join(c.alternatives[:5])}_"
                   if c.alternatives else "")
            lines.append(f"| {CONFIDENCE_MARK[c.confidence]} {c.confidence} "
                         f"| {c.runtime} | {name} | {c.evidence}{alt} |")
        lines.append("")

    if report.unmatched_runtime:
        lines += ["## Observed but not found in source", ""]
        lines += [f"- {item}" for item in report.unmatched_runtime[:50]]
        lines.append("")

    if report.unmatched_source:
        lines += [
            "## In source but not observed", "",
            "_Dead code, or simply not reached by this crawl — which of those "
            "it is, this report cannot tell you._", "",
        ]
        lines += [f"- {item}" for item in report.unmatched_source[:50]]
        lines.append("")

    return "\n".join(lines)


def _esc(value: object) -> str:
    return html.escape(str(value))


def _html_sections(report: CorrelationReport) -> str:
    out = ""
    for kind, title in _SECTIONS:
        rows = [c for c in report.correlations if c.kind == kind]
        if not rows:
            continue
        body = ""
        for c in sorted(rows, key=lambda r: CONFIDENCE_ORDER.index(r.confidence)):
            ref = (f"<br><code>{_esc(c.ref.path)}:{_esc(c.ref.line)}</code>"
                   if c.ref else "")
            alts = (f"<br><i>alternatives: "
                    f"{_esc(', '.join(c.alternatives[:5]))}</i>"
                    if c.alternatives else "")
            body += (
                f"<tr><td><span class='conf {_esc(c.confidence)}'>"
                f"{_esc(c.confidence)}</span></td>"
                f"<td>{_esc(c.runtime)}</td>"
                f"<td>{_esc(c.source_name or '—')}{ref}</td>"
                f"<td class='meta'>{_esc(c.evidence)}{alts}</td></tr>"
            )
        out += (f"<h2>{_esc(title)} ({len(rows)})</h2><table>"
                "<thead><tr><th>Confidence</th><th>Observed</th>"
                "<th>Source</th><th>Evidence</th></tr></thead>"
                f"<tbody>{body}</tbody></table>")
    return out


_STYLE = """
  body { font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 2rem auto; max-width: 1000px; color: #1a1a1a; }
  h1 { font-size: 1.5rem; } h2 { margin-top: 2rem; font-size: 1.15rem; }
  code { background: #f4f4f5; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee;
           vertical-align: top; font-size: 13px; }
  th { background: #fafafa; }
  .meta { color: #666; font-size: 12px; }
  .kpis span { display:inline-block; background:#f4f4f5; border-radius:6px;
               padding:6px 10px; margin:3px; }
  .banner { padding:10px 12px; border-radius:6px; background:#f4f4f5;
            border-left:4px solid #9ca3af; color:#374151; }
  .conf { font-weight:700; font-size:11px; text-transform:uppercase;
          padding:2px 6px; border-radius:4px; }
  .conf.confirmed { background:#dcfce7; color:#166534; }
  .conf.high { background:#dcfce7; color:#15803d; }
  .conf.medium { background:#fef9c3; color:#854d0e; }
  .conf.low { background:#ffedd5; color:#9a3412; }
  .conf.unknown { background:#f4f4f5; color:#4b5563; }
"""


def build_html(report: CorrelationReport) -> str:
    s = report.stats
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        "<title>Runtime → Source Correlation</title>\n"
        f"<style>{_STYLE}</style></head><body>\n"
        "<h1>Runtime → Source Correlation</h1>\n"
        f'<p class="meta">Repo <code>{_esc(report.repo_path)}</code> · crawl '
        f"<code>{_esc(report.source_crawl_id)}</code> · generated "
        f"{_esc(report.generated_at)}</p>\n"
        '<p class="banner">Every link is a <b>heuristic match between two '
        "approximations</b> — a static source scan and a runtime capture. "
        "Confidence and evidence are shown for each; an ambiguous match is "
        "reported as <b>low</b> with its alternatives rather than resolved to "
        "a guess.</p>\n"
        '<div class="kpis">'
        f"<span>Correlations: <b>{_esc(s['correlations'])}</b></span>"
        f"<span>Elements: <b>{_esc(s['elements'])}</b></span>"
        f"<span>Routes: <b>{_esc(s['routes'])}</b></span>"
        f"<span>Endpoints: <b>{_esc(s['endpoints'])}</b></span>"
        f"<span>Unmatched runtime: <b>{_esc(s['unmatched_runtime'])}</b></span>"
        "</div>\n"
        f"{_html_sections(report)}\n"
        "</body></html>"
    )


def write_correlation(report: CorrelationReport, output_dir: str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(out / "correlation.json"),
        "markdown": str(out / "correlation.md"),
        "html": str(out / "correlation.html"),
    }
    Path(paths["json"]).write_text(
        json.dumps(report.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["markdown"]).write_text(build_markdown(report), encoding="utf-8")
    Path(paths["html"]).write_text(build_html(report), encoding="utf-8")
    return paths
