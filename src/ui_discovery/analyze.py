"""V2 CLI entry point — analyze a crawl into structure.

    python -m ui_discovery.analyze <crawl.json | output/<slug>/>

Reads an existing (immutable) crawl model and writes, alongside it:
    analysis.json     canonical analysis model (fingerprints, regions, components)
    analysis.md       Markdown analysis report
    analysis.html     HTML analysis report

No re-crawling — this operates purely on the stored crawl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .analysis import analyze_crawl
from .models import Crawl
from .reports import write_analysis


def _resolve_crawl_json(target: str) -> Path:
    p = Path(target)
    if p.is_dir():
        p = p / "crawl.json"
    if not p.exists():
        raise FileNotFoundError(f"No crawl.json found at {target}")
    return p


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ui_discovery.analyze",
        description="V2 analysis (fingerprints, regions, components) over a crawl.",
    )
    parser.add_argument(
        "target", help="Path to crawl.json or the output/<slug>/ directory."
    )
    args = parser.parse_args(argv)

    try:
        crawl_json = _resolve_crawl_json(args.target)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    crawl = Crawl.model_validate(json.loads(crawl_json.read_text(encoding="utf-8")))
    print(f"[INFO] Analyzing crawl {crawl.crawl_id} ({len(crawl.pages)} pages)")

    analysis = analyze_crawl(crawl)
    paths = write_analysis(analysis, str(crawl_json.parent))

    s = analysis.stats
    print(f"[INFO] Fingerprinted {s.get('elements_fingerprinted', 0)} elements "
          f"({s.get('unique_fingerprints', 0)} unique)")
    print(f"[INFO] Shared components: {s.get('shared_components', 0)} · "
          f"repeated: {s.get('repeated_components', 0)} · "
          f"nav menus: {s.get('navigation_menus', 0)}")
    print(f"[INFO] Wrote {paths['json']}")
    print(f"[INFO] Wrote {paths['markdown']}")
    print(f"[INFO] Wrote {paths['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
