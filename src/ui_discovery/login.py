"""CLI to capture a login session for authenticated portals.

    python -m ui_discovery.login <login-url> --output session.json

Run this LOCALLY (it opens a visible browser). Log in by hand, press Enter, and
the session is saved. Then pass it to the other commands:

    python -m ui_discovery.crawl <url> --auth-state session.json
"""

from __future__ import annotations

import sys

from .auth import capture_session


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ui_discovery.login",
        description="Capture a logged-in browser session (storage_state).",
    )
    parser.add_argument("url", help="The portal's login URL.")
    parser.add_argument("--output", default="session.json",
                        help="Where to save the session (default: session.json).")
    parser.add_argument("--headless", action="store_true",
                        help="Run headless (only for automated/pre-authed flows).")
    args = parser.parse_args(argv)

    try:
        capture_session(args.url, args.output, headless=args.headless)
    except Exception as exc:
        print(f"[ERROR] Could not capture session: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
