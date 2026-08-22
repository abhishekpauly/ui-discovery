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
from .auth import describe_session, load_storage_state, session_status
from .browser import describe_redaction as describe_aria_redaction
from .cliconfig import (
    add_config_argument,
    authorized_or_exit,
    crawl_options,
    describe,
    load_or_exit,
    redaction_policy,
    resolve_output_dir,
    resolve_output_root,
    safety_policy,
)
from .config import Scope
from .crawler import crawl_site
from .extraction import describe_redaction as describe_element_redaction
from .inventory import attach_metrics, write_inventory, write_module_artifacts
from .network import describe_redaction as describe_network_redaction
from .redact import describe_redaction as describe_content_redaction
from .relations import build_relations
from .reports import (
    write_analysis,
    write_documentation,
    write_qaplan,
    write_reports,
    write_semantics,
)
from .run import RunContext, command_line, config_digest
from .safety import describe_envelope
from .util import slug_for

STAGES = ("crawl", "analyze", "semantic", "docgen", "qagen")


def data_handling_posture(scope: Scope) -> dict:
    """G3: assemble what this capture will deliberately not keep.

    Composed from the three modules that actually enforce it — `network` drops
    headers, bodies and sensitive query values; `browser` strips typed text out
    of the ARIA snapshot; `extraction` keeps only choice-shaped element values.
    Each describes its own rules, so this function orders and merges and
    invents nothing. The moment it starts describing a rule itself is the
    moment the manifest can disagree with the engine.

    `never_persisted` and `redactions` are kept apart deliberately: the first
    never enters the model, the second is seen and dropped on the way out. The
    second is the weaker promise and the one worth enumerating.
    """
    network = describe_network_redaction(tuple(scope.privacy.redact_network_keys))
    aria = describe_aria_redaction()
    element = describe_element_redaction()
    # G5 rides on G3's posture rather than inventing a second section: what the
    # engine removes from *displayed* content is the same kind of promise as
    # what it removes from a URL, and a reader should find them together.
    content = describe_content_redaction(redaction_policy(scope))
    return {
        "never_persisted": [
            *network["never_persisted"],
            "the session itself — only whether one was used, its source, "
            "and when it expires",
        ],
        "redactions": [
            *network["redactions"],
            *element["redactions"],
            *aria["redactions"],
            *content["redactions"],
        ],
        "network_keys_extra": network["network_keys_extra"],
        "value_recorded_for": element["value_recorded_for"],
        # Present whether or not redaction is on. A capture has to say which
        # posture it ran under, or a reader cannot tell a clean capture from an
        # unredacted one.
        "content_redaction": content["content_redaction"],
    }


def _run_stage(name: str, fn: Callable, results: dict,
               run: Optional[RunContext] = None) -> bool:
    """Run one stage, reporting cleanly on failure. Returns success.

    A failed stage is a warning, not an abort: the crawl above it is the
    expensive artifact and stays on disk either way. `run` records the timing
    and the outcome — this wrapper already brackets every stage, which makes it
    the one place that has to know a stage happened.
    """
    print(f"\n[INFO] --- {name} ---")
    if run is None:
        try:
            fn()
            return True
        except Exception as exc:
            print(f"[WARN] Stage {name!r} failed: {exc}", file=sys.stderr)
            results.setdefault("failed", []).append(name)
            return False
    try:
        with run.stage(name):
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
    parser.add_argument(
        "--probe", action="store_true", default=None,
        help="Force the safe interaction probe on, overriding a config that "
             "disables it. On by default.")
    parser.add_argument(
        "--no-probe", action="store_true", default=None,
        help="Do not interact with the target at all: no clicking, so no "
             "modals, menus or tab panels are opened and no API traffic is "
             "observed. Probing is on by default because a capture that never "
             "clicks anything misses most of what a portal is. To keep it on "
             "but scope it down, use the `probe:` block in a scope config.",
    )
    parser.add_argument(
        "--no-state-capture", action="store_true", default=None,
        help="Probe as usual, but do not photograph the modals, menus and "
             "panels that open.",
    )
    parser.add_argument(
        "--no-component-screenshots", action="store_true", default=None,
        help="Do not take cropped screenshots of forms, dialogs, tab panels "
             "and data tables.",
    )
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
        "--no-deep-nav", action="store_true",
        help="Do not click elements the app never marked up as links. "
             "Deep navigation is on by default because some portals put "
             "whole sections behind them; turn it off for a faster, "
             "link-following-only capture.",
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
    # G1: before a URL is resolved, a session is read or a browser exists.
    authorized_or_exit(scope)

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
    # Pre-flight: a session that has already lapsed should cost a second,
    # not a full crawl of login screens.
    for line in describe_session(auth_state, auth_path, start_url):
        print(line, file=sys.stderr if line.startswith("[ERROR]") else None)
    if auth_state and session_status(auth_state, start_url).get("expired"):
        return 2

    if scope.auth.required and not auth_state:
        print("[ERROR] Config sets auth.required: true but no session was "
              "supplied.", file=sys.stderr)
        return 1

    out_dir = resolve_output_dir(scope, args.output, slug_for(start_url))
    skip = set(args.skip)
    results: dict = {}

    # O1-O3: one id for the whole run, an event stream beside the capture, and
    # a manifest at the end. Started here because this is the first point at
    # which the output folder is known — everything before it is argument
    # handling that cannot fail in an interesting way.
    run = RunContext.begin(
        str(out_dir), target=start_url,
        # O5: the index sits at the root the captures are written under, so it
        # spans every run against every target rather than one folder's worth.
        index_dir=resolve_output_root(scope, args.output))
    status = session_status(auth_state, start_url) if auth_state else {}
    run.describe(
        config_file=args.config,
        config_sha256=config_digest(scope),
        command=command_line(),
        authorized=scope.authorized,
        authorized_by=scope.authorized_by,
        environment=scope.environment,
        auth_used=bool(auth_state),
        auth_source=status.get("source"),
        auth_expires_in_hours=(
            round(status["seconds_remaining"] / 3600, 1)
            if status.get("seconds_remaining") else None),
        # G2: the rules this run operates under, known before it starts. The
        # probe profiles are resolved during the crawl and folded in below —
        # recording the rest up front means a run that dies mid-crawl still
        # says what it would have refused.
        safety={**describe_envelope(safety_policy(scope)),
                "submit_forms": scope.safety.submit_forms},
        # G3: what this capture will deliberately not keep. Assembled from the
        # modules that enforce each rule rather than restated here, so the
        # manifest cannot claim a guarantee the engine does not make.
        data_handling=data_handling_posture(scope),
    )

    # --- crawl (the one stage that must succeed) ---------------------------
    options = crawl_options(scope, args)
    print("[INFO] --- crawl ---")
    print(f"[INFO] Crawling {start_url} (max_pages={options.max_pages}, "
          f"max_depth={options.max_depth}, probe={options.probe})")
    try:
        with run.stage("crawl"):
            crawl = asyncio.run(crawl_site(
                start_url, output_dir=str(out_dir), auth_state=auth_state,
                options=options, run=run,
            ))
            run.count(pages=crawl.stats.pages_crawled,
                      links=crawl.stats.links_discovered)
    except Exception as exc:
        print(f"[ERROR] Crawl failed: {exc}", file=sys.stderr)
        run.finish("failed")
        return 1
    crawl.config.config_file = args.config
    crawl.run_id = run.run_id
    run.crawl_id = crawl.crawl_id
    # G2: which probe profile applied where is only resolved by the crawl, so
    # the envelope is completed rather than declared. `describe` merges, so the
    # nested dict has to be rebuilt whole or the earlier keys would be dropped.
    run.describe(safety={**run.safety_envelope(),
                         "probe_profiles": crawl.config.probe_profiles})
    # Computed once and reused: the report, the relations artifact and docgen
    # must describe the same graph, not three independently-derived ones.
    relations = build_relations(crawl)
    write_reports(crawl, str(out_dir), relations=relations)
    write_inventory(crawl, str(out_dir))
    modules = write_module_artifacts(
        crawl, str(out_dir), [(m.name, m.start_url) for m in scope.modules])
    s = crawl.stats
    print(f"[INFO] Crawled {s.pages_crawled} pages "
          f"({s.pages_failed} failed) in {s.runtime_seconds}s")
    if modules:
        print(f"[INFO] Module folders: {', '.join(sorted(modules))}")
    run.record_stats(
        pages_crawled=s.pages_crawled, pages_failed=s.pages_failed,
        elements=sum(n.page.counts.get("total_elements", 0) for n in crawl.pages),
        navigation_edges=relations.stats.get("navigation_edges", 0),
        forms=relations.stats.get("forms", 0),
        tables=relations.stats.get("tables", 0),
        states_captured=sum(len(n.probe.states) for n in crawl.pages if n.probe),
        auth_expired=s.auth_expired,
        # O4: what interacting with every page actually cost, which is the
        # question `QA.3` asks and the one nobody could previously answer.
        probe_ms=s.probe_ms,
    )

    analysis = semantics = probe_model = None
    if crawl.pages and crawl.pages[0].probe:
        probe_model = crawl.pages[0].probe

    if "analyze" in skip:
        run.skipped("analyze", "excluded with --skip")
    if "analyze" not in skip:
        def _analyze():
            nonlocal analysis
            analysis = analyze_crawl(crawl)
            write_analysis(analysis, str(out_dir))
            a = analysis.stats
            run.count(unique_elements=a.get("unique_fingerprints", 0),
                      shared_components=a.get("shared_components", 0))
            print(f"[INFO] {a.get('unique_fingerprints', 0)} unique elements · "
                  f"{a.get('shared_components', 0)} shared components")
        _run_stage("analyze", _analyze, results, run)

    if "semantic" in skip:
        run.skipped("semantic", "excluded with --skip")
    elif analysis is None:
        run.skipped("semantic", "analysis unavailable")
    if "semantic" not in skip and analysis is not None:
        def _semantic():
            nonlocal semantics
            from .semantic import classify_analysis, get_provider, refine_semantics

            semantics = classify_analysis(analysis)
            provider = get_provider(args.provider, args.model)
            if provider is not None:
                semantics = refine_semantics(semantics, provider)
            write_semantics(semantics, str(out_dir))
            run.count(labels=len(semantics.labels))
            print(f"[INFO] Labelled {len(semantics.labels)} elements "
                  f"({semantics.provider})")
        _run_stage("semantic", _semantic, results, run)

    if "docgen" in skip:
        run.skipped("docgen", "excluded with --skip")
    if "docgen" not in skip:
        def _docgen():
            from .docgen import generate as generate_doc

            doc = generate_doc(crawl, analysis, semantics, args.provider,
                               args.model, relations=relations)
            write_documentation(doc, str(out_dir))
            run.count(pages_documented=len(doc.pages))
            print(f"[INFO] Documented {len(doc.pages)} pages")
        _run_stage("docgen", _docgen, results, run)

    if "qagen" in skip:
        run.skipped("qagen", "excluded with --skip")
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
            run.count(scenarios=len(plan.scenarios))
            print(f"[INFO] {len(plan.scenarios)} candidate scenarios · "
                  f"skeletons -> {skeleton.name}")
        _run_stage("qagen", _qagen, results, run)

    manifest = run.finish()
    # O4: the timings only exist once every stage has, so the metrics block is
    # spliced into the summary now rather than rendered with it after the crawl.
    attach_metrics(manifest.model_dump(mode="json"), str(out_dir))
    print(f"\n[INFO] Capture complete -> {out_dir}")
    print(f"[INFO] Run {manifest.run_id} · {manifest.outcome} · "
          f"{manifest.duration_ms // 1000}s · {manifest.event_count} events "
          f"-> run.json, events.jsonl")
    metrics = manifest.metrics
    if metrics.get("ms_per_page"):
        probe_share = metrics.get("probe_share_of_crawl_pct")
        print(f"[INFO] {metrics['pages']} screens · "
              f"{metrics['ms_per_page'] / 1000:.1f}s per screen"
              + (f" · probing was {probe_share}% of the crawl"
                 if probe_share else "")
              + f" · slowest stage: {metrics.get('slowest_stage')}")
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
