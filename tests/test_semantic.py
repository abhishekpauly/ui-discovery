"""V5.1 semantic classification tests — deterministic default + mock refine.

All zero-token: the deterministic classifier needs no provider, and MockProvider
is a pure offline stand-in. No real LLM is called here.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

from ui_discovery.analysis import analyze_crawl
from ui_discovery.crawler import crawl_site
from ui_discovery.models import ElementFingerprint
from ui_discovery.reports import write_semantics
from ui_discovery.semantic import (
    MockProvider,
    classify_analysis,
    classify_fingerprint,
    get_provider,
    refine_semantics,
)

SITE = Path(__file__).resolve().parents[1] / "fixtures" / "site"


def fp(**kw) -> ElementFingerprint:
    base = dict(fingerprint="f1", component_signature="c1", strategy="structural",
                category="button", role="button", accessible_name="OK",
                landmark="main", dom_path="button:nth-of-type(1)")
    base.update(kw)
    return ElementFingerprint(**base)


# --- deterministic classifier (unit) ----------------------------------------

def test_destructive_button():
    lab = classify_fingerprint(fp(accessible_name="Delete account"), "u")
    assert lab.label == "destructive" and lab.source == "deterministic"


def test_primary_action_button():
    assert classify_fingerprint(fp(accessible_name="Create customer"), "u").label \
        == "primary_action"


def test_secondary_action_button():
    assert classify_fingerprint(fp(accessible_name="Details"), "u").label \
        == "secondary_action"


def test_navigation_link():
    lab = classify_fingerprint(
        fp(category="link", role="link", accessible_name="Home", landmark="navigation"), "u")
    assert lab.label == "navigation"


def test_filter_input():
    lab = classify_fingerprint(
        fp(category="input", role="searchbox", accessible_name="Search", landmark="form"), "u")
    assert lab.label == "filter"


def test_form_input():
    lab = classify_fingerprint(
        fp(category="input", role="textbox", accessible_name="Email", landmark="form"), "u")
    assert lab.label == "form_input"


def test_table_is_data_display():
    assert classify_fingerprint(fp(category="table", role="table", accessible_name="Orders"), "u").label \
        == "data_display"


# --- integration over a real analysis ---------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def analysis():
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        crawl = asyncio.run(crawl_site(f"http://127.0.0.1:{port}/index.html",
                                       max_depth=3, output_dir="/tmp/uidisco_sem"))
    finally:
        httpd.shutdown()
    return analyze_crawl(crawl)


def test_deterministic_over_real_analysis(analysis, tmp_path):
    sem = classify_analysis(analysis)
    assert sem.provider == "deterministic"
    assert sem.stats["total"] > 0
    assert sem.stats.get("llm_refined", 0) == 0
    # the "Create customer" button on the customers page is a primary action
    assert any(l.label == "primary_action" for l in sem.labels)
    # nav links are navigation
    assert any(l.label == "navigation" and l.landmark == "navigation" for l in sem.labels)
    # reports write out
    paths = write_semantics(sem, str(tmp_path))
    for p in paths.values():
        assert Path(p).exists()


def test_provider_none_is_deterministic():
    assert get_provider("none") is None


def test_mock_refine_marks_llm_and_reclassifies(analysis):
    sem = classify_analysis(analysis)
    before_nav = sum(1 for l in sem.labels if l.label == "navigation")
    sem = refine_semantics(sem, MockProvider())
    assert sem.provider == "mock"
    # MockProvider promotes table-row "View" links to secondary_action
    assert sem.stats["llm_refined"] > 0
    assert any(l.source == "llm" for l in sem.labels)
    assert any(l.label == "secondary_action" and (l.accessible_name or "") == "View"
               for l in sem.labels)
    # and it genuinely changed something vs the deterministic pass
    after_nav = sum(1 for l in sem.labels if l.label == "navigation")
    assert after_nav < before_nav
