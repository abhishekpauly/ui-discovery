"""V3 CLI entry point — probe a single page's behavior, safely.

    python -m ui_discovery.probe <url> [--max-interactions N] [--output DIR]

Writes into <output>/<slug>/:
    probe.json        interactions (with before/after) + network observations
    probe.md          Markdown probe report
    probe.html        HTML probe report

Only structurally-safe, reversible controls are ever clicked (see safety.py).
Network is recorded as method/url/status only, with secrets redacted.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .auth import load_storage_state
from .cliconfig import (
    add_config_argument,
    describe,
    load_or_exit,
    pick,
    resolve_output_dir,
    safety_policy,
)
from .interactions import probe_page
from .reports import write_probe
from .util import slug_for


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ui_discovery.probe",
        description="V3 safe interaction + network probe for a single page.",
    )
    parser.add_argument("url", nargs="?", default=None,
                        help="URL to probe. Optional if the config sets start_url.")
    add_config_argument(parser)
    parser.add_argument("--max-interactions", type=int, default=None,
                        help="Max safe interactions to execute.")
    parser.add_argument("--output", default=None, help="Output directory.")
    parser.add_argument("--headed", action="store_true", help="Run browser headed.")
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

    print(f"[INFO] Probing {url}")
    try:
        probe = probe_page(
            url,
            max_interactions=pick(args.max_interactions,
                                  scope.budget.max_interactions, 40),
            headless=not args.headed,
            auth_state=auth_state,
            policy=safety_policy(scope),
        )
    except Exception as exc:
        print(f"[ERROR] Probe failed: {exc}", file=sys.stderr)
        return 1

    paths = write_probe(probe, str(out_dir))
    s = probe.stats
    print(f"[INFO] Elements seen: {s.get('elements_seen', 0)} · "
          f"executed: {s.get('executed', 0)} · "
          f"refused(block/caution): {s.get('blocked', 0)}/{s.get('caution', 0)}")
    print(f"[INFO] State-changing interactions: {s.get('state_changing', 0)}")
    print(f"[INFO] Network: {s.get('network_requests', 0)} requests "
          f"({s.get('api_requests', 0)} API)")
    print(f"[INFO] Wrote {paths['json']}")
    print(f"[INFO] Wrote {paths['markdown']}")
    print(f"[INFO] Wrote {paths['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
