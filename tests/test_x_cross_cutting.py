"""X1 (pipeline) and X5 (politeness).

The pipeline's contract is that it orchestrates rather than reimplements, and
that a failure in a *reporting* stage never costs you the crawl — the crawl is
the expensive artifact.

Politeness defaults are today's behavior, so nothing slows down unless asked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ui_discovery.cliconfig import crawl_options
from ui_discovery.config import Scope
from ui_discovery.crawler import crawl_site


class _Args:
    """Stand-in for an argparse namespace with nothing specified."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# --- X5: politeness ---------------------------------------------------------

def test_politeness_defaults_are_todays_behaviour():
    p = Scope().politeness
    assert p.max_requests_per_minute is None   # unlimited
    assert p.max_concurrency == 100
    assert p.respect_robots_txt is False


def test_politeness_read_from_config():
    scope = Scope.model_validate({"politeness": {
        "max_requests_per_minute": 30,
        "max_concurrency": 2,
        "respect_robots_txt": True,
    }})
    opts = crawl_options(scope, _Args())
    assert opts.max_requests_per_minute == 30
    assert opts.max_concurrency == 2
    assert opts.respect_robots_txt is True


def test_politeness_flag_beats_config():
    scope = Scope.model_validate(
        {"politeness": {"max_requests_per_minute": 30}})
    opts = crawl_options(scope, _Args(max_requests_per_minute=5.0))
    assert opts.max_requests_per_minute == 5.0


def test_rate_limited_crawl_still_completes(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=1, max_pages=3,
        output_dir=str(tmp_path),
        max_requests_per_minute=600, max_concurrency=1,
    ))
    assert crawl.pages


def test_concurrency_cap_is_accepted(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=1, max_pages=2,
        output_dir=str(tmp_path), max_concurrency=1,
    ))
    assert crawl.pages


# --- X1: pipeline -----------------------------------------------------------

def _run_pipeline(argv: list[str]) -> int:
    from ui_discovery.pipeline import main

    return main(argv)


def test_pipeline_produces_every_artifact(serve, tmp_path):
    site = serve("fixtures/site")
    code = _run_pipeline([
        site.url("index.html"), "--output", str(tmp_path),
        "--max-depth", "1", "--max-pages", "3",
    ])
    assert code == 0

    out = next(tmp_path.iterdir())
    produced = {p.name for p in out.iterdir()}
    for expected in ("crawl.json", "report.md", "analysis.json",
                     "semantics.json", "documentation.json", "qa.json",
                     "generated_tests.py"):
        assert expected in produced, f"{expected} missing from {sorted(produced)}"


def test_pipeline_respects_skip(serve, tmp_path):
    site = serve("fixtures/site")
    code = _run_pipeline([
        site.url("index.html"), "--output", str(tmp_path),
        "--max-depth", "0", "--max-pages", "1",
        "--skip", "docgen", "--skip", "qagen",
    ])
    assert code == 0

    produced = {p.name for p in next(tmp_path.iterdir()).iterdir()}
    assert "analysis.json" in produced
    assert "documentation.json" not in produced
    assert "qa.json" not in produced


def test_pipeline_is_deterministic_without_a_provider(serve, tmp_path):
    site = serve("fixtures/site")
    _run_pipeline([site.url("index.html"), "--output", str(tmp_path),
                   "--max-depth", "0", "--max-pages", "1"])
    out = next(tmp_path.iterdir())
    qa = json.loads((out / "qa.json").read_text(encoding="utf-8"))
    assert qa["provider"] == "deterministic"


def test_pipeline_requires_a_start_url(tmp_path):
    assert _run_pipeline(["--output", str(tmp_path)]) == 1


def test_pipeline_refuses_an_out_of_scope_start_url(tmp_path):
    config = tmp_path / "scope.json"
    config.write_text(json.dumps({"scope": {"exclude": ["/admin/**"]}}))
    code = _run_pipeline([
        "https://x.test/admin/users", "--config", str(config),
        "--output", str(tmp_path),
    ])
    assert code == 1


def test_a_failing_report_stage_does_not_lose_the_crawl(
    serve, tmp_path, monkeypatch,
):
    """The crawl is the expensive artifact — a broken report generator must
    never be the reason it is discarded."""
    import ui_discovery.pipeline as pipeline_mod

    def boom(*a, **k):
        raise RuntimeError("docgen exploded")

    monkeypatch.setattr("ui_discovery.docgen.generate", boom)

    site = serve("fixtures/site")
    code = pipeline_mod.main([
        site.url("index.html"), "--output", str(tmp_path),
        "--max-depth", "0", "--max-pages", "1",
    ])
    assert code == 0  # the run is not a failure

    produced = {p.name for p in next(tmp_path.iterdir()).iterdir()}
    assert "crawl.json" in produced       # survived
    assert "analysis.json" in produced    # earlier stage survived
    assert "documentation.json" not in produced
    assert "qa.json" in produced          # later stages still ran


def test_pipeline_and_crawl_resolve_settings_identically(tmp_path):
    # Both commands go through crawl_options, so the same config cannot
    # produce different crawls depending on which entry point was used.
    scope = Scope.model_validate({
        "budget": {"max_pages": 7, "max_depth": 2},
        "capabilities": {"probe": True, "screenshots": False},
        "identity": {"dedupe_queries": True},
    })
    opts = crawl_options(scope, _Args())
    assert opts.max_pages == 7
    assert opts.max_depth == 2
    assert opts.probe is True
    assert opts.screenshots is False
    assert opts.dedupe_queries is True


# --- readiness: "hasn't started" is not "settled" ----------------------------

def test_an_app_shell_is_not_mistaken_for_a_settled_page():
    """Regression: a page that has not begun rendering has an identical DOM on
    every poll, so a plain equality check declared it stable after ~500ms and
    every later stage faithfully recorded zero elements.

    Found on a live dashboard, where it also tripped the H4 expiry alarm — a
    healthy session reported as rejected.
    """
    from ui_discovery.extraction import extract_page

    url = (Path(__file__).resolve().parents[1]
           / "fixtures" / "edge" / "slow_shell.html").resolve().as_uri()
    page = extract_page(url)

    names = {e.accessible_name for e in page.elements if e.accessible_name}
    assert "Late button" in names, "captured the shell before it rendered"
    assert page.readiness["dom_stable"] is True
    # It must have actually waited past the shell, not returned at ~500ms.
    assert page.readiness["dom_stable_wait_ms"] >= 1000


def test_a_genuinely_empty_page_reports_not_stable():
    """The honest answer for a page that never renders: not stable, and no
    content ever seen — which is what H4's empty-page check should react to."""
    from ui_discovery.browser import has_rendered

    # fields: htmlLen : nodeCount : renderedTextLen : interactiveCount
    assert has_rendered("5000:300:800:12") is True    # a real page
    assert has_rendered("40:9:0:1") is True           # sparse, but has a control
    assert has_rendered("4000:12:0:0") is False       # shell: all inline script
    assert has_rendered("25:8:0:0") is False          # `<div id="root"></div>`
    assert has_rendered("") is False
    assert has_rendered("garbage") is False


# --- CrawlOptions -----------------------------------------------------------

def test_crawl_options_defaults_are_the_engine_defaults():
    from ui_discovery import CrawlOptions

    o = CrawlOptions()
    assert (o.max_pages, o.max_depth) == (25, 3)
    assert o.headless is True
    assert o.screenshots is True
    assert o.probe is False


def test_keyword_overrides_still_work_on_crawl_site():
    # The whole point of the refactor: 40-odd existing call sites keep
    # passing plain keywords and nothing about them changes.
    import inspect

    from ui_discovery.crawler import crawl_site

    params = list(inspect.signature(crawl_site).parameters)
    assert params == ["start_url", "output_dir", "auth_state", "options",
                      "overrides"]


def test_options_replace_ignores_unset_flags_but_honours_meaningful_none():
    from ui_discovery import CrawlOptions

    base = CrawlOptions()
    # `None` from an unset argparse flag must not wipe out a real default...
    assert base.replace(max_depth=None).max_depth == 3
    # ...but `None` is a real value for the nullable fields.
    assert base.replace(include=None).include is None


def test_a_mistyped_option_is_still_an_error():
    from ui_discovery import CrawlOptions

    with pytest.raises(TypeError):
        CrawlOptions().replace(max_dpeth=2)


def test_options_are_reusable_across_crawls(serve, tmp_path):
    from ui_discovery import CrawlOptions

    site = serve("fixtures/site")
    options = CrawlOptions(max_depth=0, max_pages=1, screenshots=False)
    first = asyncio.run(crawl_site(site.url("index.html"),
                                   output_dir=str(tmp_path / "a"),
                                   options=options))
    second = asyncio.run(crawl_site(site.url("about.html"),
                                    output_dir=str(tmp_path / "b"),
                                    options=options))
    assert first.pages and second.pages
    assert all(n.page.screenshot_path is None for n in first.pages)
    assert all(n.page.screenshot_path is None for n in second.pages)
