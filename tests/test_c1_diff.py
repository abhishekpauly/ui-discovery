"""C1 — deterministic change diff between two snapshots.

The fixture pair (`fixtures/diff/v1` -> `v2`) encodes exactly the changes the
acceptance criteria call for:

  * `reports.html` is an **added page**
  * "Export all" is a **removed button**
  * "Create customer" -> "Add customer" is a **rename with a stable id**
    (fingerprint survives, so `match="fingerprint"`)
  * "Filter list" -> "Refine list" is a **rename with no stable id**
    (fingerprint moves with the name, so `match="structural"`)
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.analysis import analyze_crawl
from ui_discovery.crawler import crawl_site
from ui_discovery.diff import diff_analyses


def _analyze(serve, version: str, tmp_path, port: int):
    # Both versions are served at the *same* origin, one after the other: a
    # snapshot's page URLs (and the fingerprints that embed them) must differ
    # only because the site changed, never because the test moved ports.
    site = serve(f"fixtures/diff/{version}", port=port)
    try:
        crawl = asyncio.run(
            crawl_site(
                site.url("index.html"),
                max_depth=2,
                output_dir=str(tmp_path / version),
            )
        )
    finally:
        site.stop()
    return analyze_crawl(crawl)


def _analyze_pair(serve, tmp_path):
    from tests.conftest import _free_port

    port = _free_port()
    old = _analyze(serve, "v1", tmp_path, port)
    new = _analyze(serve, "v2", tmp_path, port)
    return old, new


@pytest.fixture
def diff(serve, tmp_path):
    old, new = _analyze_pair(serve, tmp_path)
    return diff_analyses(old, new)


def _named(diff, kind, name):
    return [c for c in diff.elements
            if c.kind == kind and (c.accessible_name or "") == name]


def test_added_page_is_reported(diff):
    added = [p for p in diff.pages if p.kind == "added"]
    assert len(added) == 1
    assert added[0].url.endswith("reports.html")
    assert diff.stats["pages_added"] == 1


def test_removed_button_is_reported(diff):
    removed = _named(diff, "removed", "Export all")
    assert len(removed) == 1
    assert removed[0].page_url.endswith("index.html")


def test_rename_with_stable_id_matches_by_fingerprint(diff):
    renamed = [c for c in diff.elements
               if c.kind == "renamed" and c.accessible_name == "Add customer"]
    assert len(renamed) == 1
    assert renamed[0].previous_name == "Create customer"
    assert renamed[0].match == "fingerprint"


def test_rename_without_stable_id_matches_structurally(diff):
    renamed = [c for c in diff.elements
               if c.kind == "renamed" and c.accessible_name == "Refine list"]
    assert len(renamed) == 1
    assert renamed[0].previous_name == "Filter list"
    assert renamed[0].match == "structural"


def test_renames_are_not_double_counted_as_add_plus_remove(diff):
    # The whole point of rename detection: these must NOT also appear as an
    # added control and a removed control.
    assert not _named(diff, "added", "Add customer")
    assert not _named(diff, "removed", "Create customer")
    assert not _named(diff, "added", "Refine list")
    assert not _named(diff, "removed", "Filter list")


def test_stats_are_consistent_with_the_element_list(diff):
    s = diff.stats
    assert s["elements_renamed"] == sum(
        1 for c in diff.elements if c.kind == "renamed")
    assert s["elements_added"] == sum(
        1 for c in diff.elements if c.kind == "added")
    assert s["elements_removed"] == sum(
        1 for c in diff.elements if c.kind == "removed")
    assert s["total_changes"] > 0


def test_diff_of_a_snapshot_against_itself_is_empty(serve, tmp_path):
    from tests.conftest import _free_port

    analysis = _analyze(serve, "v1", tmp_path, _free_port())
    d = diff_analyses(analysis, analysis)
    assert d.stats["total_changes"] == 0
    assert d.pages == []
    assert d.elements == []
    assert d.components == []


def test_diff_is_deterministic(serve, tmp_path):
    old, new = _analyze_pair(serve, tmp_path)
    first = diff_analyses(old, new)
    second = diff_analyses(old, new)
    # generated_at is the only field allowed to move between runs.
    assert first.model_dump(exclude={"generated_at"}) == \
        second.model_dump(exclude={"generated_at"})


def test_reports_written(diff, tmp_path):
    from pathlib import Path

    from ui_discovery.reports import write_diff

    paths = write_diff(diff, str(tmp_path / "out"))
    for p in paths.values():
        assert Path(p).exists()

    md = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "UI Change Diff" in md
    assert "Renamed controls" in md
    assert "“Create customer” → **“Add customer”**" in md

    page_html = Path(paths["html"]).read_text(encoding="utf-8")
    assert "UI Change Diff" in page_html
    assert "Renamed controls" in page_html
