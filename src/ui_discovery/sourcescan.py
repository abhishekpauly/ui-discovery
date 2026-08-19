"""V4 CLI — index a frontend repo, and optionally correlate it to a crawl.

    python -m ui_discovery.sourcescan <repo>                    # index only
    python -m ui_discovery.sourcescan <repo> --crawl output/<slug>/

Writes `source_index.json`, and when a crawl is given, `correlation.json`
plus Markdown/HTML reports. The repo is only ever read — nothing is executed,
installed or built.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import Crawl
from .sourcemap import correlate, index_repo
from .sourcemap.reports import write_correlation, write_source_index


def _resolve_crawl_json(target: str) -> Path:
    p = Path(target)
    if p.is_dir():
        p = p / "crawl.json"
    if not p.exists():
        raise FileNotFoundError(f"No crawl.json found at {target}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.sourcescan",
        description="V4 source indexing + runtime correlation (read-only).",
    )
    parser.add_argument("repo", help="Path to the frontend repo.")
    parser.add_argument(
        "--crawl", default=None,
        help="crawl.json or output/<slug>/ to correlate against.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Where to write (default: alongside the crawl, else ./output).",
    )
    args = parser.parse_args(argv)

    try:
        index = index_repo(args.repo)
    except (NotADirectoryError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    s = index.stats
    print(f"[INFO] Scanned {s['files_scanned']} source files")
    print(f"[INFO] Components: {s['components']} · routes: {s['routes']} · "
          f"endpoints: {s['endpoints']}")

    out_dir = args.output or "output"
    crawl = None
    if args.crawl:
        try:
            crawl_json = _resolve_crawl_json(args.crawl)
        except FileNotFoundError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        crawl = Crawl.model_validate(
            json.loads(crawl_json.read_text(encoding="utf-8"))
        )
        out_dir = args.output or str(crawl_json.parent)

    paths = write_source_index(index, out_dir)
    print(f"[INFO] Wrote {paths['json']}")

    if crawl is None:
        print("[INFO] No --crawl given; skipping correlation.")
        return 0

    report = correlate(crawl, index)
    cs = report.stats
    print(f"[INFO] Correlated {cs['correlations']} items "
          f"(elements {cs['elements']}, routes {cs['routes']}, "
          f"endpoints {cs['endpoints']})")
    print(f"[INFO] Confidence — confirmed {cs['confidence_confirmed']} · "
          f"high {cs['confidence_high']} · medium {cs['confidence_medium']} · "
          f"low {cs['confidence_low']}")
    print(f"[INFO] Unmatched: {cs['unmatched_runtime']} runtime, "
          f"{cs['unmatched_source']} source")

    if not crawl.config.probe:
        print("[INFO] This crawl ran without --probe, so no API traffic was "
              "observed and no endpoints could be correlated.")

    written = write_correlation(report, out_dir)
    for key in ("json", "markdown", "html"):
        print(f"[INFO] Wrote {written[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
