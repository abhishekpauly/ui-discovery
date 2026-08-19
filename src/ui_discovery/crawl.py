"""V1 CLI entry point — crawl a same-domain site into a UI Crawl Report.

    python -m ui_discovery.crawl <url> [--max-pages N] [--max-depth N] [--output DIR]

Writes into <output>/<slug>/:
    crawl.json        canonical, versioned crawl model
    report.md         Markdown report
    report.html       HTML report
    screenshots/      one full-page screenshot per crawled page
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .auth import load_storage_state
from .cliconfig import (
    add_config_argument,
    crawl_options,
    describe,
    load_or_exit,
    resolve_output_dir,
)
from .crawler import crawl_site
from .inventory import write_inventory
from .reports import write_reports
from .util import slug_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.crawl",
        description="V1 UI crawler (Crawlee + Playwright) -> UI Crawl Report.",
    )
    # `url` is optional: a scope config can carry `start_url:` instead.
    parser.add_argument("url", nargs="?", default=None,
                        help="Start URL (http(s)://). Optional if the config "
                             "sets start_url.")
    add_config_argument(parser)
    # Config-backed flags default to None so "not typed" is distinguishable
    # from "typed the same as the default" — see cliconfig.pick.
    parser.add_argument("--max-pages", type=int, default=None, help="Page budget.")
    parser.add_argument("--max-depth", type=int, default=None, help="Depth budget.")
    parser.add_argument("--output", default=None, help="Output directory.")
    parser.add_argument(
        "--headless", action="store_true",
        help="Hide the browser. The crawler runs headed by default so "
             "you can watch it; use this for CI or unattended runs.",
    )
    parser.add_argument(
        "--auth-state", default=None,
        help="Path to a saved session (see: python -m ui_discovery.login).",
    )
    parser.add_argument(
        "--dedupe-queries", action="store_true", default=None,
        help="Collapse query-string variants that differ only in noise "
             "params (utm_*, session ids, ...) into one page identity.",
    )
    parser.add_argument(
        "--drop-param", action="append", default=None, metavar="NAME",
        help="Extra query param name to treat as noise (repeatable). "
             "Only takes effect with --dedupe-queries.",
    )
    parser.add_argument(
        "--hash-routes", action="store_true", default=None,
        help="Treat `#/route`-style hash fragments as distinct pages "
             "(for SPAs that route client-side via the hash).",
    )
    parser.add_argument(
        "--probe", action="store_true", default=None,
        help="Also run the safe interaction + network probe on every crawled "
             "page. Only structurally-safe, reversible controls are clicked; "
             "destructive ones are never executed.",
    )
    parser.add_argument(
        "--max-interactions", type=int, default=None,
        help="Per-page interaction budget when --probe is set (default: 40).",
    )
    parser.add_argument(
        "--no-screenshots", action="store_true", default=None,
        help="Skip screenshots.",
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
        help="Exit non-zero if a saved session turns out to be expired "
             "(a login page was reached while authenticated).",
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

    auth_state_path = args.auth_state or scope.auth.state_file
    try:
        auth_state = load_storage_state(auth_state_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if scope.auth.required and not auth_state:
        print("[ERROR] This config sets auth.required: true but no session "
              "was supplied. Pass --auth-state, or set auth.state_file.",
              file=sys.stderr)
        return 1

    options = crawl_options(scope, args)
    out_dir = resolve_output_dir(scope, args.output, slug_for(start_url))

    print(f"[INFO] Crawling {start_url} "
          f"(max_pages={options.max_pages}, max_depth={options.max_depth})")
    try:
        crawl = asyncio.run(
            crawl_site(
                start_url,
                output_dir=str(out_dir),
                auth_state=auth_state,
                options=options,
            )
        )
    except Exception as exc:
        print(f"[ERROR] Crawl failed: {exc}", file=sys.stderr)
        return 1

    # Record which config produced this snapshot — the audit trail S1 exists
    # for is worthless if the capture doesn't name the scope it ran under.
    crawl.config.config_file = args.config
    paths = write_reports(crawl, str(out_dir))
    # Every run leaves the plain-facts artifacts behind, not just the model.
    write_inventory(crawl, str(out_dir))
    s = crawl.stats
    print(f"[INFO] Crawled {s.pages_crawled} pages "
          f"({s.pages_failed} failed) in {s.runtime_seconds}s")
    print(f"[INFO] Navigation edges: {s.links_discovered}")
    print(f"[INFO] Wrote {paths['json']}")
    print(f"[INFO] Wrote {paths['markdown']}")
    print(f"[INFO] Wrote {paths['html']}")
    print(f"[INFO] Artifacts in {out_dir}: summary.md · urls.txt · "
          f"elements.csv · endpoints.md · screenshots/")

    # H4: say it loudly. Crawling a wall of login screens and reporting
    # "success" is the failure this check exists to prevent.
    if s.auth_expired:
        symptom = []
        if s.pages_logged_out:
            symptom.append(f"{s.pages_logged_out} look logged-out")
        if s.pages_empty:
            symptom.append(f"{s.pages_empty} rendered nothing at all")
        print(
            f"\n[ERROR] Session appears REJECTED — of {s.pages_crawled} "
            f"crawled pages, {' and '.join(symptom)}.\n"
            f"         This capture is of the login/blank state, not the "
            f"product. Re-capture the session:\n"
            f"         python -m ui_discovery.login {start_url} "
            f"--output {auth_state_path or 'session.json'}",
            file=sys.stderr,
        )
        if args.fail_on_auth_expiry:
            return 2
    elif s.pages_logged_out or s.pages_empty:
        print(f"[WARN] {s.pages_logged_out} page(s) look logged-out, "
              f"{s.pages_empty} rendered nothing "
              f"(no session was supplied).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
