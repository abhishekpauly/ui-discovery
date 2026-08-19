"""Analysis orchestrator: a `Crawl` in, an `Analysis` out.

Pure and deterministic — no browser, no network. Operates entirely on the
stored crawl model, honoring the append-only principle (the crawl is never
mutated).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import SCHEMA_VERSION, __version__
from ..models import Analysis, Crawl, PageAnalysis
from .components import detect_components
from .fingerprint import fingerprint_element
from .navigation import extract_navigations
from .regions import regions_for_page


def analyze_crawl(crawl: Crawl) -> Analysis:
    per_page_fps: dict[str, list] = {}
    page_analyses: list[PageAnalysis] = []

    for node in crawl.pages:
        page = node.page
        fps = [fingerprint_element(el, node.url) for el in page.elements]
        per_page_fps[node.url] = fps
        page_analyses.append(
            PageAnalysis(
                url=node.url,
                title=page.title,
                depth=node.depth,
                regions=regions_for_page(page),
                fingerprints=fps,
            )
        )

    components = detect_components(per_page_fps)
    navigations = extract_navigations(crawl.pages)

    total_elements = sum(len(f) for f in per_page_fps.values())
    unique_fps = len({fp.fingerprint for f in per_page_fps.values() for fp in f})
    strategy_counts: dict[str, int] = {}
    for f in per_page_fps.values():
        for fp in f:
            strategy_counts[fp.strategy] = strategy_counts.get(fp.strategy, 0) + 1

    stats = {
        "pages_analyzed": len(page_analyses),
        "elements_fingerprinted": total_elements,
        "unique_fingerprints": unique_fps,
        "shared_components": sum(1 for c in components if c.kind == "shared"),
        "repeated_components": sum(1 for c in components if c.kind == "repeated"),
        "navigation_menus": len(navigations),
        **{f"fp_strategy_{k}": v for k, v in strategy_counts.items()},
    }

    return Analysis(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        source_crawl_id=crawl.crawl_id,
        start_url=crawl.config.start_url,
        stats=stats,
        pages=page_analyses,
        components=components,
        navigations=navigations,
    )
