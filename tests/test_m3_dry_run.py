"""M3 — the same answer, from the command you were going to run anyway.

`M2` gave the engine a `map` command. `M3` is the recognition that a separate
command is one someone has to remember exists: the person about to spend forty
minutes on a crawl is typing `crawl`, not `map`, and the preview has to be
reachable from there or it will not be used.

Two properties, and both are easy to lose in a way no reader would notice:

  * **It opens no browser.** Asserted on the absence of a launch rather than on
    elapsed time — a timing assertion keeps passing on a fast machine long
    after the property has gone.
  * **It predicts the run you are actually about to start.** It sits after
    config resolution and authorization and before anything opens, and CLI
    flags win over the config exactly as they do on a real run. A preview that
    skipped a gate, or ignored `--max-pages`, would be describing a different
    crawl — which is worse than no preview, because it would be trusted.
"""

from __future__ import annotations

import json

import pytest

from ui_discovery.crawl import main as crawl_main
from ui_discovery.models import UrlMap
from ui_discovery.pipeline import main as pipeline_main


@pytest.fixture
def no_browser(monkeypatch):
    """Any attempt to launch Chromium fails the test outright."""
    import playwright.async_api
    import playwright.sync_api

    def refuse(*args, **kwargs):
        raise AssertionError("a dry run opened a browser")

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", refuse)
    monkeypatch.setattr(playwright.async_api, "async_playwright", refuse)


@pytest.fixture
def site(serve):
    return serve("fixtures/sitemap")


def _written_map(tmp_path) -> UrlMap:
    path = next(tmp_path.rglob("map.json"))
    return UrlMap.model_validate(json.loads(path.read_text(encoding="utf-8")))


# --- crawl --dry-run --------------------------------------------------------


def test_a_dry_run_navigates_nothing(site, tmp_path, no_browser, capsys):
    assert crawl_main([site.url("index.html"), "--dry-run",
                       "--output", str(tmp_path)]) == 0
    assert "nothing was navigated" in capsys.readouterr().out


def test_a_dry_run_writes_the_map(site, tmp_path, no_browser):
    assert crawl_main([site.url("index.html"), "--dry-run",
                       "--output", str(tmp_path)]) == 0
    url_map = _written_map(tmp_path)
    assert url_map.stats["total"] > 1
    assert next(tmp_path.rglob("urls.txt")).read_text(encoding="utf-8")


def test_a_dry_run_reports_the_budget_verdict(site, tmp_path, no_browser,
                                              capsys):
    """The question it exists to answer: does what I want to capture fit in
    what I am willing to spend?"""
    assert crawl_main([site.url("index.html"), "--dry-run", "--max-pages", "2",
                       "--output", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "budget:max_pages=2" in out
    assert _written_map(tmp_path).stats["in_scope"] == 2


def test_the_flag_wins_over_the_config(site, tmp_path, no_browser):
    """A preview that read the config's budget while the operator passed a
    flag would describe a different crawl from the one about to start."""
    assert crawl_main([site.url("index.html"), "--dry-run", "--max-pages", "1",
                       "--output", str(tmp_path)]) == 0
    assert _written_map(tmp_path).stats["max_pages"] == 1


def test_a_dry_run_still_applies_the_scope_rules(site, tmp_path, no_browser):
    """It sits after config resolution on purpose. A preview that skipped the
    gates would predict a crawl that does not happen."""
    config = tmp_path / "scope.yaml"
    config.write_text(
        f"start_url: {site.url('index.html')}\n"
        "scope:\n  exclude: ['/reports/**']\n", encoding="utf-8")

    assert crawl_main(["--config", str(config), "--dry-run",
                       "--output", str(tmp_path)]) == 0
    entries = _written_map(tmp_path).entries
    refused = [e for e in entries if e.decided_by == "exclude:/reports/**"]
    assert refused, [e.model_dump() for e in entries]
    assert all(not e.in_scope for e in refused)


def test_a_config_whose_budget_cannot_reach_a_module_says_so_by_name(
        site, tmp_path, no_browser, capsys):
    """A verdict of "3 of 12" does not tell an operator that Reports is the
    module they are about to lose. The module names are the only part of a
    scope config written in their language rather than the engine's."""
    config = tmp_path / "scope.yaml"
    config.write_text(
        f"start_url: {site.url('index.html')}\n"
        "budget:\n  max_pages: 1\n"
        "modules:\n"
        f"  - name: Reports\n    start_url: {site.url('reports/quarterly.html')}\n",
        encoding="utf-8")

    assert crawl_main(["--config", str(config), "--dry-run",
                       "--output", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Reports" in out
    assert "not reachable" in out

    unreachable = _written_map(tmp_path).stats["modules_unreachable"]
    assert [m["module"] for m in unreachable] == ["Reports"]
    assert unreachable[0]["decided_by"].startswith("budget:")


def test_a_module_excluded_by_a_pattern_is_named_too(site, tmp_path,
                                                     no_browser, capsys):
    config = tmp_path / "scope.yaml"
    config.write_text(
        f"start_url: {site.url('index.html')}\n"
        "scope:\n  exclude: ['/reports/**']\n"
        "modules:\n"
        f"  - name: Reports\n    start_url: {site.url('reports/quarterly.html')}\n",
        encoding="utf-8")

    assert crawl_main(["--config", str(config), "--dry-run",
                       "--output", str(tmp_path)]) == 0
    assert "Reports" in capsys.readouterr().out
    unreachable = _written_map(tmp_path).stats["modules_unreachable"]
    assert unreachable[0]["decided_by"] == "exclude:/reports/**"


def test_a_reachable_module_is_not_reported(site, tmp_path, no_browser):
    """The warning has to stay quiet when nothing is wrong, or it becomes
    noise people learn to skip past."""
    config = tmp_path / "scope.yaml"
    config.write_text(
        f"start_url: {site.url('index.html')}\n"
        "budget:\n  max_pages: 50\n"
        "modules:\n"
        f"  - name: Reports\n    start_url: {site.url('reports/quarterly.html')}\n",
        encoding="utf-8")

    assert crawl_main(["--config", str(config), "--dry-run",
                       "--output", str(tmp_path)]) == 0
    assert _written_map(tmp_path).stats["modules_unreachable"] == []


# --- pipeline --dry-run -----------------------------------------------------


def test_the_pipeline_dry_runs_too(site, tmp_path, no_browser, capsys):
    """`pipeline` is the command most people type, and it is the expensive
    one. A preview reachable only from `crawl` would be reachable from the
    wrong place."""
    assert pipeline_main([site.url("index.html"), "--dry-run",
                          "--output", str(tmp_path)]) == 0
    assert "nothing was navigated" in capsys.readouterr().out
    assert _written_map(tmp_path).stats["total"] > 1


def test_a_dry_run_writes_no_capture(site, tmp_path, no_browser):
    """It must not leave something that looks like a capture behind — a
    `crawl.json` from a run that never happened would be worse than nothing."""
    assert pipeline_main([site.url("index.html"), "--dry-run",
                          "--output", str(tmp_path)]) == 0
    written = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {"map.json", "urls.txt"}, written
