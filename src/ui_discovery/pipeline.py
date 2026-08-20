"""X1 — one command for a full capture.

    python -m ui_discovery.pipeline <url> [--config scope.yaml]

Runs crawl → analyze → semantic → docgen → qagen in order, into one output
directory, so a complete capture is a single invocation instead of five.

Each stage is the same function the individual CLI calls — this orchestrates,
it does not reimplement. Stages are skipped when the config turns them off,
and a stage that fails does not discard the ones before it: the crawl is the
expensive part, and losing it because a later report failed would be the
worst possible trade.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Callable, Optional

from .analysis import analyze_crawl
from .auth import load_storage_state
from .cliconfig import (
    add_config_argument,
    crawl_options,
    describe,
    load_or_exit,
    resolve_output_dir,
)
from .crawler import crawl_site
from .inventory import write_inventory, write_module_artifacts
from .reports import (
    write_analysis,
    write_documentation,
    write_qaplan,
    write_reports,
    write_semantics,
)
from .util import slug_for

STAGES = ("crawl", "analyze", "semantic", "docgen", "qagen")


def _run_stage(name: str, fn: Callable, results: dict) -> bool:
    """Run one stage, reporting cleanly on failure. Returns success.

    A failed stage is a warning, not an abort: the crawl above it is the
    expensive artifact and stays on disk either way.
    """
    print(f"\n[INFO] --- {name} ---")
    try:
        fn()
        return True
    except Exception as exc:
        print(f"[WARN] Stage {name!r} failed: {exc}", file=sys.stderr)
        results.setdefault("failed", []).append(name)
        return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.pipeline",
        description="X1: crawl -> analyze -> semantic -> docgen -> qagen "
                    "in one command.",
    )
    parser.add_argument("url", nargs="?", default=None,
                        help="Start URL. Optional if the config sets start_url.")
    add_config_argument(parser)
    parser.add_argument("--output", default=None, help="Output directory.")
    parser.add_argument(
        "--headless", action="store_true",
        help="Hide the browser. The crawler runs headed by default so "
             "you can watch it; use this for CI or unattended runs.",
    )
    parser.add_argument("--auth-state", default=None,
                        help="Path to a saved session.")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--probe", action="store_true", default=None,
                        help="Run the safe interaction probe on every page.")
    parser.add_argument("--max-interactions", type=int, default=None)
    parser.add_argument("--dedupe-queries", action="store_true", default=None)
    parser.add_argument("--drop-param", action="append", default=None)
    parser.add_argument("--hash-routes", action="store_true", default=None)
    parser.add_argument("--no-screenshots", action="store_true", default=None)
    parser.add_argument(
        "--provider", default="none",
        help="Optional LLM for the V5 stages (none|mock|anthropic|openai). "
             "Every stage is deterministic without it.",
    )
    parser.add_argument("--model", default=None, help="Provider model override.")
    parser.add_argument(
        "--lang", default="py", choices=("py", "ts"),
        help="Language for exported Playwright skeletons.",
    )
    parser.add_argument(
        "--skip", action="append", default=[], choices=STAGES, metavar="STAGE",
        help=f"Skip a stage (repeatable). One of: {', '.join(STAGES)}",
    )
    parser.add_argument(
        "--deep-nav", action="store_true", default=None,
        help="Click elements the app never marked up as links (no anchor, no "
             "button, no ARIA role — just a pointer cursor) to find routes "
             "nothing else can reach. Labels are still safety-checked, so "
             "destructive controls are refused.",
    )
    parser.add_argument(
        "--seed", action="append", default=None, metavar="URL",
        help="Extra start URL (repeatable). Use for routes nothing links to "
             "— a contextual sidebar, or a nav item that is a click handler "
             "rather than a link. Also settable as `modules:` in a config.",
    )
    parser.add_argument(
        "--no-reveal-nav", action="store_true",
        help="Do not expand collapsed navigation before reading links. "
             "Revealing is on by default and finds routes hidden behind "
             "accordions and overflow menus.",
    )
    parser.add_argument(
        "--max-requests-per-minute", type=float, default=None,
        help="X5: cap request rate across the crawl. Use on shared or "
             "production-adjacent targets.",
    )
    parser.add_argument(
        "--max-concurrency", type=int, default=None,
        help="X5: upper bound on parallel pages (default 100; Crawlee "
             "autoscales below it).",
    )
    parser.add_argument(
        "--respect-robots-txt", action="store_true", default=None,
        help="X5: honour the target's robots.txt.",
    )
    parser.add_argument(
        "--fail-on-auth-expiry", action="store_true",
        help="Exit non-zero if the saved session turns out to be rejected.",
    )
    args = parser.parse_args(argv)

    scope = load_or_exit(args.config)
    for line in describe(scope, args.config):
        print(line)

    try:
        start_url = scope.resolve_start_url(args.url)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    auth_path = args.auth_state or scope.auth.state_file
    try:
        auth_state = load_storage_state(auth_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if scope.auth.required and not auth_state:
        print("[ERROR] Config sets auth.required: true but no session was "
              "supplied.", file=sys.stderr)
        return 1

    out_dir = resolve_output_dir(scope, args.output, slug_for(start_url))
    skip = set(args.skip)
    results: dict = {}

    # --- crawl (the one stage that must succeed) ---------------------------
    options = crawl_options(scope, args)
    print("[INFO] --- crawl ---")
    print(f"[INFO] Crawling {start_url} (max_pages={options.max_pages}, "
          f"max_depth={options.max_depth}, probe={options.probe})")
    try:
        crawl = asyncio.run(crawl_site(
            start_url, output_dir=str(out_dir), auth_state=auth_state,
            options=options,
        ))
    except Exception as exc:
        print(f"[ERROR] Crawl failed: {exc}", file=sys.stderr)
        return 1
    crawl.config.config_file = args.config
    write_reports(crawl, str(out_dir))
    write_inventory(crawl, str(out_dir))
    modules = write_module_artifacts(
        crawl, str(out_dir), [(m.name, m.start_url) for m in scope.modules])
    s = crawl.stats
    print(f"[INFO] Crawled {s.pages_crawled} pages "
          f"({s.pages_failed} failed) in {s.runtime_seconds}s")

    analysis = semantics = probe_model = None
    if crawl.pages and crawl.pages[0].probe:
        probe_model = crawl.pages[0].probe

    if "analyze" not in skip:
        def _analyze():
            nonlocal analysis
            analysis = analyze_crawl(crawl)
            write_analysis(analysis, str(out_dir))
            a = analysis.stats
            print(f"[INFO] {a.get('unique_fingerprints', 0)} unique elements · "
                  f"{a.get('shared_components', 0)} shared components")
        _run_stage("analyze", _analyze, results)

    if "semantic" not in skip and analysis is not None:
        def _semantic():
            nonlocal semantics
            from .semantic import classify_analysis, get_provider, refine_semantics

            semantics = classify_analysis(analysis)
            provider = get_provider(args.provider, args.model)
            if provider is not None:
                semantics = refine_semantics(semantics, provider)
            write_semantics(semantics, str(out_dir))
            print(f"[INFO] Labelled {len(semantics.labels)} elements "
                  f"({semantics.provider})")
        _run_stage("semantic", _semantic, results)

    if "docgen" not in skip:
        def _docgen():
            from .docgen import generate as generate_doc

            doc = generate_doc(crawl, analysis, semantics, args.provider, args.model)
            write_documentation(doc, str(out_dir))
            print(f"[INFO] Documented {len(doc.pages)} pages")
        _run_stage("docgen", _docgen, results)

    if "qagen" not in skip:
        def _qagen():
            from pathlib import Path

            from .qagen import build_playwright
            from .qagen import generate as generate_qa

            plan = generate_qa(crawl, analysis, semantics, probe_model,
                               args.provider, args.model, args.lang)
            write_qaplan(plan, str(out_dir))
            ext = "spec.ts" if args.lang == "ts" else "py"
            skeleton = Path(out_dir) / f"generated_tests.{ext}"
            skeleton.write_text(build_playwright(plan), encoding="utf-8")
            print(f"[INFO] {len(plan.scenarios)} candidate scenarios · "
                  f"skeletons -> {skeleton.name}")
        _run_stage("qagen", _qagen, results)

    print(f"\n[INFO] Capture complete -> {out_dir}")
    if results.get("failed"):
        print(f"[WARN] Stages that failed: {', '.join(results['failed'])}. "
              f"The crawl itself is intact.", file=sys.stderr)

    if s.auth_expired:
        print(f"\n[ERROR] Session appears REJECTED — of {s.pages_crawled} "
              f"crawled pages, {s.pages_logged_out} look logged-out and "
              f"{s.pages_empty} rendered nothing. This capture is of the "
              f"login/blank state, not the product.\n"
              f"         python -m ui_discovery.login {start_url} "
              f"--output {auth_path or 'session.json'}", file=sys.stderr)
        if args.fail_on_auth_expiry:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
