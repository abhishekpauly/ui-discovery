"""V5.4 — the change narrative over C1's diff.

The property that matters: **the diff stays the source of truth**. The
narrative is deterministic by default, an LLM may only rephrase it, and a
provider that fails or refuses must leave a useful summary behind rather
than an empty one.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.analysis import analyze_crawl
from ui_discovery.crawler import crawl_site
from ui_discovery.diff import diff_analyses
from ui_discovery.narrate import build_narrative, narrate


def _analyze(serve, version: str, tmp_path, port: int):
    site = serve(f"fixtures/diff/{version}", port=port)
    try:
        crawl = asyncio.run(crawl_site(
            site.url("index.html"), max_depth=2,
            output_dir=str(tmp_path / version),
        ))
    finally:
        site.stop()
    return analyze_crawl(crawl)


@pytest.fixture
def diff(serve, tmp_path):
    from tests.conftest import _free_port

    port = _free_port()
    old = _analyze(serve, "v1", tmp_path, port)
    new = _analyze(serve, "v2", tmp_path, port)
    return diff_analyses(old, new)


# --- deterministic by default -----------------------------------------------

def test_narrative_is_deterministic_without_a_provider(diff):
    result = narrate(diff)
    assert result.narrative
    assert result.narrative_source == "deterministic"


def test_same_diff_gives_the_same_narrative(diff):
    first = build_narrative(diff)
    second = build_narrative(diff)
    assert first == second


def test_narrative_mentions_the_actual_changes(diff):
    text = build_narrative(diff)
    # The fixture pair renames "Create customer" -> "Add customer".
    assert "Add customer" in text
    assert "Create customer" in text
    assert "renamed" in text.lower()


def test_narrative_flags_why_renames_matter(diff):
    # A summary that lists changes without saying what to do about them is
    # just the tables again in prose.
    assert "tests" in build_narrative(diff).lower()


def test_narrative_hedges_on_intent(diff):
    # Two snapshots cannot know whether a change was deliberate.
    assert "intended" in build_narrative(diff).lower()


def test_empty_diff_says_so_plainly(diff, serve, tmp_path):
    from tests.conftest import _free_port

    same = _analyze(serve, "v1", tmp_path, _free_port())
    unchanged = diff_analyses(same, same)
    text = build_narrative(unchanged)
    assert "nothing changed" in text.lower()


# --- the diff remains the source of truth -----------------------------------

def test_narrating_never_mutates_the_structured_findings(diff):
    before = diff.model_dump(exclude={"narrative", "narrative_source"})
    narrate(diff, "mock")
    after = diff.model_dump(exclude={"narrative", "narrative_source"})
    assert before == after


def test_provider_replaces_only_the_prose(diff):
    narrate(diff, "mock")
    assert diff.narrative_source == "mock"
    assert diff.narrative.startswith("[mock-generated]")
    # The tables are untouched and still say what they said.
    assert diff.stats["elements_renamed"] == 2


@pytest.mark.parametrize("returns", ["", None])
def test_a_provider_that_produces_nothing_degrades_gracefully(
    diff, monkeypatch, returns,
):
    """A refusing, failing or empty provider must leave the deterministic
    summary behind — an empty narrative would be worse than no feature."""
    class Silent:
        name = "silent"

        def complete(self, prompt, *, max_tokens=1500):
            return returns

    from ui_discovery import llm

    monkeypatch.setattr(llm, "get_text_provider",
                        lambda name, model=None: Silent())

    result = narrate(diff, "silent")
    assert result.narrative
    assert "Add customer" in result.narrative  # the deterministic text
    assert result.narrative_source == "deterministic"


def test_no_provider_means_no_llm_import(diff):
    import sys

    narrate(diff)  # provider "none"
    assert "anthropic" not in sys.modules
    assert "openai" not in sys.modules


# --- reports ----------------------------------------------------------------

def test_narrative_appears_in_the_reports(diff, tmp_path):
    from pathlib import Path

    from ui_discovery.reports import write_diff

    narrate(diff)
    paths = write_diff(diff, str(tmp_path))
    md = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "## What changed" in md
    assert diff.narrative.splitlines()[0] in md

    page_html = Path(paths["html"]).read_text(encoding="utf-8")
    assert "What changed" in page_html


def test_ai_drafted_narrative_is_labelled(diff, tmp_path):
    from pathlib import Path

    from ui_discovery.reports import write_diff

    narrate(diff, "mock")
    md = Path(write_diff(diff, str(tmp_path))["markdown"]).read_text(
        encoding="utf-8")
    # A reader must be able to tell prose from findings at a glance.
    assert "AI-drafted" in md
