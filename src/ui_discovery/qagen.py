"""V5.3 — QA / test-scenario generation (and Playwright skeleton export, C2).

**Deterministic by default (zero tokens).** From crawl (+ analysis + semantics +
probe if present) it produces candidate test scenarios — smoke, navigation,
form, destructive-guard, interaction — and emits **runnable Playwright test
skeletons** built from the stable role + accessible-name selectors already
captured. Destructive controls are never automated; they become explicit
"do-not-automate" guards.

**Optional LLM strategy, quarantined** (principle #13): `--provider ...` writes a
short test-strategy narrative on top; providers load lazily (`ui_discovery.llm`),
live only under `[semantic]`, and never change the deterministic scenarios.

    python -m ui_discovery.qagen output/<slug>/ [--provider none|mock|...] [--lang py|ts]
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from . import SCHEMA_VERSION, __version__
from .llm import get_text_provider
from .models import (
    Analysis,
    Crawl,
    InteractionProbe,
    QAPlan,
    Semantics,
    TestScenario,
    TestStep,
)
from .util import normalize_url, slug_for

_ROLE_FALLBACK = {
    "link": "link", "button": "button", "input": "textbox",
    "select": "combobox", "textarea": "textbox", "nav": "navigation",
    "image": "img", "table": "table",
}


def _load(path: Path, model):
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _controls_by_label(sem: Optional[Semantics], url: str, page) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if sem:
        for lab in sem.labels:
            if lab.page_url == url and lab.accessible_name:
                out.setdefault(lab.label, [])
                if lab.accessible_name not in out[lab.label]:
                    out[lab.label].append(lab.accessible_name)
        return out
    # fallback: group by category
    for e in page.elements:
        if e.accessible_name and e.category in ("button", "link", "input", "select", "textarea"):
            out.setdefault(e.category, [])
            if e.accessible_name not in out[e.category]:
                out[e.category].append(e.accessible_name)
    return out


def _role_for(name: str, page) -> Optional[str]:
    for e in page.elements:
        if e.accessible_name == name:
            return e.role or _ROLE_FALLBACK.get(e.category)
    return None


def _internal_links(page, base_url: str, crawl_urls: dict[str, str]) -> list[tuple]:
    out, seen = [], set()
    for e in page.elements:
        if e.category != "link" or not e.accessible_name:
            continue
        href = e.attributes.get("href")
        if not href:
            continue
        target = normalize_url(urljoin(page.final_url or base_url, href))
        if target in crawl_urls and target != normalize_url(base_url) and e.accessible_name not in seen:
            seen.add(e.accessible_name)
            out.append((e.accessible_name, e.role or "link", target, crawl_urls[target]))
    return out[:3]


def generate_scenarios(
    crawl: Crawl,
    analysis: Optional[Analysis],
    semantics: Optional[Semantics],
    probe: Optional[InteractionProbe],
) -> list[TestScenario]:
    scenarios: list[TestScenario] = []
    crawl_urls = {n.url: (n.page.title or n.url) for n in crawl.pages}

    for node in crawl.pages:
        page, url = node.page, node.url
        sslug = slug_for(url)
        labels = _controls_by_label(semantics, url, page)

        # 1. Smoke
        steps = [TestStep(action="navigate", target=url)]
        if page.title:
            steps.append(TestStep(action="assert_title", target=page.title))
        key: list[str] = []
        for lab in ("primary_action", "navigation", "data_display", "filter",
                    "form_input", "secondary_action"):
            for nm in labels.get(lab, [])[:2]:
                if nm not in key:
                    key.append(nm)
        for nm in key[:6]:
            steps.append(TestStep(action="assert_visible", target=nm, role=_role_for(nm, page)))
        scenarios.append(TestScenario(
            id=f"{sslug_safe(sslug)}-smoke",
            title=f"Smoke: {page.title or url}", page_url=url, type="smoke",
            priority="P1", automatable=True, steps=steps,
            expected="Page loads and its key controls are visible."))

        # 2. Navigation
        for i, (nm, role, turl, ttitle) in enumerate(_internal_links(page, url, crawl_urls)):
            scenarios.append(TestScenario(
                id=f"{sslug_safe(sslug)}-nav-{i}",
                title=f"Navigate: {page.title} → {ttitle}", page_url=url,
                type="navigation", priority="P2", automatable=True,
                steps=[TestStep(action="navigate", target=url),
                       TestStep(action="click", target=nm, role=role),
                       TestStep(action="assert_url", target=turl)],
                expected=f"Clicking “{nm}” navigates to {ttitle}."))

        # 3. Form (fill-only; never submit)
        form_ctrls = labels.get("form_input", []) + labels.get("filter", []) \
            + labels.get("input", []) + labels.get("textarea", [])
        if form_ctrls:
            fsteps = [TestStep(action="navigate", target=url)]
            for nm in form_ctrls[:4]:
                fsteps.append(TestStep(action="fill", target=nm,
                                       role=_role_for(nm, page), value="sample"))
            fsteps.append(TestStep(action="guard_skip", target="submit",
                                   note="Do NOT auto-submit — needs test data & authorization."))
            scenarios.append(TestScenario(
                id=f"{sslug_safe(sslug)}-form", title=f"Form input: {page.title or url}",
                page_url=url, type="form", priority="P2", automatable=False,
                steps=fsteps, notes="Fill-only; submission is out of scope for automation.",
                expected="Inputs accept values; submission is NOT automated."))

        # 4. Destructive guard (negative/safety)
        destr = labels.get("destructive", [])
        if destr:
            scenarios.append(TestScenario(
                id=f"{sslug_safe(sslug)}-guard", title=f"Destructive guard: {page.title or url}",
                page_url=url, type="destructive_guard", priority="P1", automatable=False,
                steps=[TestStep(action="navigate", target=url)] +
                      [TestStep(action="guard_skip", target=nm,
                                note="destructive — must never be automated") for nm in destr],
                notes="Verify these require human confirmation; never automate them.",
                expected="Destructive actions require explicit human confirmation."))

    # 5. Interaction (if a probe.json is present)
    if probe:
        for i, it in enumerate(x for x in probe.interactions if x.executed):
            scenarios.append(TestScenario(
                id=f"probe-int-{i}", title=f"Interaction: {it.target}",
                page_url=probe.final_url, type="interaction", priority="P2",
                automatable=True,
                steps=[TestStep(action="navigate", target=probe.final_url),
                       TestStep(action="click", target=it.target, role=it.role),
                       TestStep(action="assert_visible", target=it.target, role=it.role)],
                expected="Activating the control changes page state (reversible)."))
    return scenarios


def sslug_safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:48] or "page"


def _stats(scen: list[TestScenario]) -> dict[str, int]:
    stats: dict[str, int] = {"total": len(scen),
                             "automatable": sum(1 for s in scen if s.automatable)}
    for s in scen:
        stats[s.type] = stats.get(s.type, 0) + 1
    return stats


def generate(crawl, analysis, semantics, probe, provider_name="none",
             model=None, language="py") -> QAPlan:
    scen = generate_scenarios(crawl, analysis, semantics, probe)
    plan = QAPlan(
        schema_version=SCHEMA_VERSION, engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_crawl_id=crawl.crawl_id, start_url=crawl.config.start_url,
        provider="deterministic", language=language,
        stats=_stats(scen), scenarios=scen,
        strategy=("Deterministic scenarios cover per-page smoke checks, "
                  "navigation, form fill (no submit), and destructive-control "
                  "guards. Automatable scenarios are exported as Playwright "
                  "skeletons; destructive actions are never automated."))

    provider = get_text_provider(provider_name, model)
    if provider is not None:
        plan.provider = provider.name
        titles = "; ".join(s.title for s in scen[:30])
        out = provider.complete(
            "Write a 3-4 sentence QA test-strategy summary for a web app, based "
            "only on these candidate scenario titles:\n" + titles)
        if out:
            plan.strategy, plan.strategy_source = out, "llm"
    return plan


# --- Playwright skeleton export (C2) ----------------------------------------

def _last_path_re(url: str) -> str:
    seg = url.rsplit("/", 1)[-1] or url
    return re.escape(seg)


def build_playwright(plan: QAPlan) -> str:
    if plan.language == "ts":
        return _build_ts(plan)
    return _build_py(plan)


def _build_py(plan: QAPlan) -> str:
    lines = [
        "# Auto-generated Playwright test skeletons (deterministic, no AI).",
        "# Review, wire to your Playwright setup, and add data/assertions.",
        "import re",
        "from playwright.sync_api import Page, expect",
        "",
    ]
    for s in plan.scenarios:
        fn = f"test_{sslug_safe(s.id)}"
        lines.append(f"def {fn}(page: Page) -> None:")
        lines.append(f"    # {s.title}  [{s.type}, {s.priority}]")
        if not s.automatable:
            lines.append(f"    # NOT AUTOMATED: {s.notes or s.expected}")
            for st in s.steps:
                if st.action == "guard_skip":
                    lines.append(f"    # SKIP (guard): {st.target!r} — {st.note}")
            lines.append("    pass")
            lines.append("")
            continue
        for st in s.steps:
            lines.append("    " + _py_step(st))
        lines.append("")
    return "\n".join(lines)


def _py_step(st: TestStep) -> str:
    if st.action == "navigate":
        return f"page.goto({st.target!r})"
    if st.action == "assert_title":
        return f"expect(page).to_have_title({st.target!r})"
    if st.action == "assert_visible":
        if st.role:
            return f"expect(page.get_by_role({st.role!r}, name={st.target!r})).to_be_visible()"
        return f"expect(page.get_by_text({st.target!r})).to_be_visible()"
    if st.action == "click":
        role = st.role or "link"
        return f"page.get_by_role({role!r}, name={st.target!r}).click()"
    if st.action == "assert_url":
        return f"expect(page).to_have_url(re.compile(r{_last_path_re(st.target)!r}))"
    if st.action == "fill":
        role = st.role or "textbox"
        return f"page.get_by_role({role!r}, name={st.target!r}).fill({st.value!r})"
    if st.action == "guard_skip":
        return f"# SKIP (guard): {st.target!r} — {st.note}"
    return f"# unknown step: {st.action}"


def _build_ts(plan: QAPlan) -> str:
    lines = [
        "// Auto-generated Playwright test skeletons (deterministic, no AI).",
        "import { test, expect } from '@playwright/test';",
        "",
    ]
    for s in plan.scenarios:
        if not s.automatable:
            lines.append(f"// NOT AUTOMATED [{s.type}]: {s.title} — {s.notes or ''}")
            continue
        lines.append(f"test({s.title!r}, async ({{ page }}) => {{")
        for st in s.steps:
            lines.append("  " + _ts_step(st))
        lines.append("});")
        lines.append("")
    return "\n".join(lines)


def _ts_step(st: TestStep) -> str:
    if st.action == "navigate":
        return f"await page.goto({st.target!r});"
    if st.action == "assert_title":
        return f"await expect(page).toHaveTitle({st.target!r});"
    if st.action == "assert_visible":
        if st.role:
            return f"await expect(page.getByRole({st.role!r}, {{ name: {st.target!r} }})).toBeVisible();"
        return f"await expect(page.getByText({st.target!r})).toBeVisible();"
    if st.action == "click":
        return f"await page.getByRole({(st.role or 'link')!r}, {{ name: {st.target!r} }}).click();"
    if st.action == "assert_url":
        return f"await expect(page).toHaveURL(/{_last_path_re(st.target)}/);"
    if st.action == "fill":
        return f"await page.getByRole({(st.role or 'textbox')!r}, {{ name: {st.target!r} }}).fill({st.value!r});"
    return f"// guard/skip: {st.target}"


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    from .reports import write_qaplan

    parser = argparse.ArgumentParser(
        prog="ui_discovery.qagen",
        description="V5.3 QA scenarios + Playwright skeletons (deterministic; optional LLM).",
    )
    parser.add_argument("target", help="output/<slug>/ directory (needs crawl.json).")
    parser.add_argument("--provider", default="none",
                        choices=["none", "mock", "anthropic", "openai"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--lang", default="py", choices=["py", "ts"],
                        help="Generated skeleton language (default: py).")
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
    probe = _load(base / "probe.json", InteractionProbe) if (base / "probe.json").exists() else None

    plan = generate(crawl, analysis, semantics, probe, args.provider, args.model, args.lang)
    paths = write_qaplan(plan, str(base))

    # Playwright skeletons
    ext = "spec.ts" if args.lang == "ts" else "py"
    skel = Path(base) / f"generated_tests.{ext}"
    skel.write_text(build_playwright(plan), encoding="utf-8")

    s = plan.stats
    print(f"[INFO] Scenarios: {s.get('total', 0)} "
          f"(automatable {s.get('automatable', 0)}) · provider {plan.provider}")
    print("[INFO] By type: " + ", ".join(f"{k} {v}" for k, v in sorted(s.items())
                                          if k not in ("total", "automatable")))
    print(f"[INFO] Wrote {paths['markdown']} / {paths['json']} and {skel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
