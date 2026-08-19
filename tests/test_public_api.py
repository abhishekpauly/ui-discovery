"""R1 — the top-level `ui_discovery` package is a real library surface, not
just a CLI. This test runs a full extract -> analyze pipeline through
`import ui_discovery` alone, touching no CLI module, to prove the public API
is complete and composable on its own.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import ui_discovery

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def test_version_and_schema_version_present():
    assert ui_discovery.__version__
    assert ui_discovery.SCHEMA_VERSION


def test_extract_and_analyze_via_public_api(tmp_path, serve):
    page = ui_discovery.extract_page(fixture_url("static.html"))
    assert page.elements

    site = serve("fixtures/site")
    crawl = asyncio.run(
        ui_discovery.crawl_site(
            site.url("index.html"), max_pages=5, output_dir=str(tmp_path)
        )
    )
    assert crawl.pages

    analysis = ui_discovery.analyze_crawl(crawl)
    assert analysis.pages

    paths = ui_discovery.write_analysis(analysis, str(tmp_path))
    assert Path(paths["json"]).exists()


def test_all_declared_exports_resolve():
    # Every name __init__ promises to export must actually resolve to a
    # callable via __getattr__, not just exist in __all__.
    for name in ui_discovery.__all__:
        if name in ("__version__", "SCHEMA_VERSION"):
            continue
        assert callable(getattr(ui_discovery, name)), name
