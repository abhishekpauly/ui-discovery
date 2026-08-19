"""V0 CLI entry point.

    python -m ui_discovery.extract <url> [--output DIR] [--headed]

Given one URL, render it and write:
    <output>/<slug>/page.json
    <output>/<slug>/screenshot.png
No crawling — this is the single-page extractor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auth import load_storage_state
from .cliconfig import (
    add_config_argument,
    describe,
    load_or_exit,
    pick,
    resolve_output_dir,
)
from .extraction import extract_page
from .util import slug_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.extract",
        description="V0 single-page UI extractor (URL -> page.json + screenshot).",
    )
    parser.add_argument("url", nargs="?", default=None,
                        help="URL to extract. Optional if the config sets start_url.")
    add_config_argument(parser)
    parser.add_argument(
        "--output", default=None, help="Output directory (default: ./output)."
    )
    parser.add_argument(
        "--headed", action="store_true", help="Run the browser headed (debug)."
    )
    parser.add_argument(
        "--timeout", type=int, default=30000, help="Navigation timeout in ms."
    )
    parser.add_argument(
        "--auth-state", default=None,
        help="Path to a saved session (see: python -m ui_discovery.login).",
    )
    args = parser.parse_args(argv)

    scope = load_or_exit(args.config)
    for line in describe(scope, args.config):
        print(line)
    try:
        url = scope.resolve_start_url(args.url)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        auth_state = load_storage_state(args.auth_state or scope.auth.state_file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    out_dir = Path(resolve_output_dir(scope, args.output, slug_for(url)))
    out_dir.mkdir(parents=True, exist_ok=True)
    # R2: capabilities.screenshots false -> pass no path, so none is taken.
    screenshot_path = (str(out_dir / "screenshot.png")
                       if scope.capabilities.screenshots else None)

    print(f"[INFO] Extracting {url}")
    try:
        page = extract_page(
            url,
            screenshot_path=screenshot_path,
            timeout_ms=args.timeout,
            headless=not args.headed,
            auth_state=auth_state,
        )
    except Exception as exc:  # surface a clean, actionable error
        print(f"[ERROR] Extraction failed: {exc}", file=sys.stderr)
        return 1

    json_path = out_dir / "page.json"
    json_path.write_text(
        json.dumps(page.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    c = page.counts
    print(f"[INFO] Title: {page.title!r}")
    print(f"[INFO] Final URL: {page.final_url}")
    print(
        "[INFO] Extracted "
        f"{c.get('total_elements', 0)} elements "
        f"({c.get('visible_elements', 0)} visible), "
        f"{c.get('headings', 0)} headings"
    )
    print(f"[INFO] Readiness: {page.readiness}")
    print(f"[INFO] Wrote {json_path}")
    if screenshot_path:
        print(f"[INFO] Screenshot {screenshot_path}")

    # H4: extracting a login page when you passed a session means it expired.
    if page.auth and page.auth.looks_logged_out:
        if auth_state:
            print(f"\n[ERROR] Session appears EXPIRED — this page looks "
                  f"logged-out ({page.auth.signal}: {page.auth.evidence}).\n"
                  f"         Re-capture it:  python -m ui_discovery.login "
                  f"{url} --output {args.auth_state or 'session.json'}", file=sys.stderr)
        else:
            print(f"[WARN] This page looks logged-out "
                  f"({page.auth.signal}). Pass --auth-state to crawl as a "
                  f"signed-in user.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
