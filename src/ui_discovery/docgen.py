"""V5.2 — documentation generation.

**Deterministic by default (zero tokens).** Assembles a UI reference document
from the structured models — crawl (required), analysis and semantics (used if
present) — with a site overview and a per-page reference: purpose, regions,
controls grouped by semantic role, links, screenshot.

**Optional LLM prose, quarantined** (principle #13): `--provider ...` has the
model write the executive overview and per-page purpose *prose* on top of the
deterministic scaffold. Providers load lazily (`ui_discovery.llm`), live only
under the `[semantic]` extra, and never mutate the source models.

    python -m ui_discovery.docgen output/<slug>/ [--provider none|mock|anthropic|openai]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import SCHEMA_VERSION, __version__
from .llm import get_text_provider
from .models import Analysis, Crawl, DocPage, Documentation, Relations, Semantics


def _load(path: Path, model):
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _controls_from_semantics(sem: Semantics, url: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for lab in sem.labels:
        if lab.page_url != url or not lab.accessible_name:
            continue
        out.setdefault(lab.label, [])
        if lab.accessible_name not in out[lab.label]:
            out[lab.label].append(lab.accessible_name)
    return out


def _controls_from_page(page) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in page.elements:
        if e.category in ("link", "button", "input", "select", "textarea") and e.accessible_name:
            out.setdefault(e.category, [])
            if e.accessible_name not in out[e.category]:
                out[e.category].append(e.accessible_name)
    return out


def _det_purpose(title: str, controls: dict[str, list[str]], screen=None) -> str:
    """A sentence about what a screen is for, built from what is on it.

    When the relationship layer is available this names the actual forms,
    tables and columns — "lets you fill in the New order form (7 fields) and
    lists Recent orders by Order, Customer, Status" — which is a description.
    Without it, it falls back to the shape-only summary, which is a category.
    """
    base = f"The “{title or 'untitled'}” page"
    if screen is not None:
        parts: list[str] = []
        real_forms = [f for f in screen.forms if f.fields]
        for form in real_forms[:3]:
            name = "" if form.name.startswith("(") else f" “{form.name}”"
            parts.append(f"lets you fill in the{name} form "
                         f"({len(form.fields)} field"
                         f"{'s' if len(form.fields) != 1 else ''})")
        for table in screen.tables[:3]:
            columns = ", ".join(table.columns[:5])
            parts.append(f"lists “{table.name}”"
                         + (f" by {columns}" if columns else ""))
        if screen.outbound:
            targets = [e.label for e in screen.outbound[:4] if e.label]
            if targets:
                parts.append("links onward to " + ", ".join(targets))
        if parts:
            return f"{base} " + "; ".join(parts) + "."

    parts = []
    if controls.get("primary_action"):
        parts.append("primary actions (" + ", ".join(controls["primary_action"][:4]) + ")")
    if controls.get("data_display") or controls.get("table"):
        parts.append("data tables")
    if controls.get("filter") or controls.get("form_input") or controls.get("input"):
        parts.append("input controls")
    if controls.get("destructive"):
        parts.append("destructive actions")
    if parts:
        return f"{base} provides " + "; ".join(parts) + "."
    return f"{base} is primarily navigational or informational."


def _inventory(crawl: Crawl) -> dict[str, int]:
    totals: Counter = Counter()
    for node in crawl.pages:
        for k, v in node.page.counts.items():
            if k in ("visible_elements", "total_elements"):
                continue
            totals[k] += v
    return dict(totals)


def generate(
    crawl: Crawl,
    analysis: Optional[Analysis] = None,
    semantics: Optional[Semantics] = None,
    provider_name: str = "none",
    model: Optional[str] = None,
    relations: Optional[Relations] = None,
) -> Documentation:
    regions_by_url = {}
    if analysis:
        regions_by_url = {
            pa.url: [f"{r.type} ({r.element_count})" for r in pa.regions]
            for pa in analysis.pages
        }
    global_nav: list[str] = []
    shared_components: list[str] = []
    if analysis:
        if analysis.navigations:
            primary = max(analysis.navigations, key=lambda n: n.page_count)
            global_nav = primary.items
        shared_components = [
            c.label for c in analysis.components
            if c.kind == "shared" and c.label
        ][:12]

    if relations is None:
        from .relations import build_relations

        relations = build_relations(crawl)
    screens = {s.url: s for s in relations.screens}
    titles = {n.url: (n.page.title or n.url) for n in crawl.pages}

    doc_pages: list[DocPage] = []
    for node in crawl.pages:
        page = node.page
        controls = (_controls_from_semantics(semantics, node.url)
                    if semantics else _controls_from_page(page))
        screen = screens.get(node.url)
        doc_pages.append(DocPage(
            url=node.url, title=page.title, depth=node.depth,
            purpose=_det_purpose(page.title, controls, screen),
            regions=regions_by_url.get(node.url, []),
            controls=controls,
            links=[u.rsplit("/", 1)[-1] or u for u in node.out_links],
            screenshot=(Path(page.screenshot_path).name if page.screenshot_path else None),
            reached_from=[
                f"“{e.label or '(unlabelled)'}” on {titles.get(e.source, e.source)}"
                for e in (screen.inbound[:6] if screen else [])
            ],
            leads_to=[
                f"“{e.label or '(unlabelled)'}” → {titles.get(e.target, e.target)}"
                for e in (screen.outbound[:8] if screen else [])
            ],
            forms=[f for f in (screen.forms if screen else []) if f.fields],
            tables=list(screen.tables) if screen else [],
            states=list(node.probe.states) if node.probe else [],
        ))

    inventory = _inventory(crawl)
    det_overview = (
        f"{len(crawl.pages)} pages crawled from {crawl.config.start_url}. "
        + (f"Global navigation: {', '.join(global_nav)}. " if global_nav else "")
        + "UI inventory: "
        + ", ".join(f"{k} {v}" for k, v in sorted(inventory.items(), key=lambda kv: -kv[1]))
        + "."
    )

    doc = Documentation(
        schema_version=SCHEMA_VERSION, engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_crawl_id=crawl.crawl_id, start_url=crawl.config.start_url,
        provider="deterministic", overview=det_overview, inventory=inventory,
        global_nav=global_nav, shared_components=shared_components, pages=doc_pages,
    )

    # Optional LLM prose layered on top of the deterministic scaffold.
    provider = get_text_provider(provider_name, model)
    if provider is not None:
        doc.provider = provider.name
        site_ctx = (f"Site: {doc.start_url}\nPages: "
                    + "; ".join(f"{p.title}" for p in doc.pages)
                    + f"\nGlobal nav: {', '.join(global_nav)}\nInventory: {inventory}")
        ov = provider.complete(
            "Write a 2-3 sentence executive overview of this web application for "
            "product documentation, based only on these facts:\n" + site_ctx)
        if ov:
            doc.overview, doc.overview_source = ov, "llm"
        for p in doc.pages:
            ctrl = "; ".join(f"{k}: {', '.join(v)}" for k, v in p.controls.items())
            desc = provider.complete(
                f"In 1-2 sentences, describe the purpose of the “{p.title}” page "
                f"for UI documentation, based only on these controls:\n{ctrl or '(none)'}")
            if desc:
                p.purpose, p.purpose_source = desc, "llm"
    return doc


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    from .reports import write_documentation

    parser = argparse.ArgumentParser(
        prog="ui_discovery.docgen",
        description="V5.2 UI documentation (deterministic; optional LLM prose).",
    )
    parser.add_argument("target", help="output/<slug>/ directory (needs crawl.json).")
    parser.add_argument("--provider", default="none",
                        choices=["none", "mock", "anthropic", "openai"],
                        help="LLM prose provider (default: none = deterministic).")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    d = Path(args.target)
    crawl_json = d / "crawl.json" if d.is_dir() else d
    if not crawl_json.exists():
        print(f"[ERROR] No crawl.json found at {args.target}", file=sys.stderr)
        return 1
    base = crawl_json.parent

    crawl = _load(crawl_json, Crawl)
    analysis = _load(base / "analysis.json", Analysis) if (base / "analysis.json").exists() else None
    semantics = _load(base / "semantics.json", Semantics) if (base / "semantics.json").exists() else None
    relations = (_load(base / "relations.json", Relations)
                 if (base / "relations.json").exists() else None)
    print(f"[INFO] Documenting {len(crawl.pages)} pages "
          f"(analysis: {'yes' if analysis else 'no'}, semantics: {'yes' if semantics else 'no'})")

    doc = generate(crawl, analysis, semantics, args.provider, args.model,
                   relations=relations)
    paths = write_documentation(doc, str(base))
    print(f"[INFO] Provider: {doc.provider} "
          f"(overview: {doc.overview_source})")
    print(f"[INFO] Wrote {paths['markdown']} / {paths['html']} / {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
