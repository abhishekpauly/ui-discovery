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
from pathlib import Path

from .auth import load_storage_state
from .crawler import crawl_site
from .reports import write_reports
from .util import slug_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.crawl",
        description="V1 UI crawler (Crawlee + Playwright) -> UI Crawl Report.",
    )
    parser.add_argument("url", help="Start URL (http(s)://).")
    parser.add_argument("--max-pages", type=int, default=25, help="Page budget.")
    parser.add_argument("--max-depth", type=int, default=3, help="Depth budget.")
    parser.add_argument("--output", default="output", help="Output directory.")
    parser.add_argument("--headed", action="store_true", help="Run browser headed.")
    parser.add_argument(
        "--auth-state", default=None,
        help="Path to a saved session (see: python -m ui_discovery.login).",
    )
    args = parser.parse_args(argv)

    try:
        auth_state = load_storage_state(args.auth_state)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.output) / slug_for(args.url)

    print(f"[INFO] Crawling {args.url} "
          f"(max_pages={args.max_pages}, max_depth={args.max_depth})")
    try:
        crawl = asyncio.run(
            crawl_site(
                args.url,
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                output_dir=str(out_dir),
                headless=not args.headed,
                auth_state=auth_state,
            )
        )
    except Exception as exc:
        print(f"[ERROR] Crawl failed: {exc}", file=sys.stderr)
        return 1

    paths = write_reports(crawl, str(out_dir))
    s = crawl.stats
    print(f"[INFO] Crawled {s.pages_crawled} pages "
          f"({s.pages_failed} failed) in {s.runtime_seconds}s")
    print(f"[INFO] Navigation edges: {s.links_discovered}")
    print(f"[INFO] Wrote {paths['json']}")
    print(f"[INFO] Wrote {paths['markdown']}")
    print(f"[INFO] Wrote {paths['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
