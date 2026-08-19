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
from .extraction import extract_page
from .util import slug_for


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.extract",
        description="V0 single-page UI extractor (URL -> page.json + screenshot).",
    )
    parser.add_argument("url", help="URL to extract (http(s):// or file://).")
    parser.add_argument(
        "--output", default="output", help="Output directory (default: ./output)."
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

    try:
        auth_state = load_storage_state(args.auth_state)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.output) / slug_for(args.url)
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = str(out_dir / "screenshot.png")

    print(f"[INFO] Extracting {args.url}")
    try:
        page = extract_page(
            args.url,
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
    print(f"[INFO] Screenshot {screenshot_path}")

    # H4: extracting a login page when you passed a session means it expired.
    if page.auth and page.auth.looks_logged_out:
        if auth_state:
            print(f"\n[ERROR] Session appears EXPIRED — this page looks "
                  f"logged-out ({page.auth.signal}: {page.auth.evidence}).\n"
                  f"         Re-capture it:  python -m ui_discovery.login "
                  f"{args.url} --output {args.auth_state}", file=sys.stderr)
        else:
            print(f"[WARN] This page looks logged-out "
                  f"({page.auth.signal}). Pass --auth-state to crawl as a "
                  f"signed-in user.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
