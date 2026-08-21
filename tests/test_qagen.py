"""V5.3 QA-generation tests — deterministic scenarios + Playwright skeletons.

Zero-token: deterministic generator needs no provider; MockTextProvider is a pure
offline stand-in. The generated Playwright source is syntax-checked with compile().
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

from ui_discovery import SCHEMA_VERSION, __version__
from ui_discovery.analysis import analyze_crawl
from ui_discovery.crawler import crawl_site
from ui_discovery.models import (
    Crawl,
    CrawlConfig,
    CrawlStats,
    Element,
    Page,
    PageNode,
    SemanticLabel,
    Semantics,
)
from ui_discovery.qagen import build_playwright, generate, generate_scenarios
from ui_discovery.semantic import classify_analysis

SITE = Path(__file__).resolve().parents[1] / "fixtures" / "site"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def context():
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        crawl = asyncio.run(crawl_site(f"http://127.0.0.1:{port}/index.html",
                                       max_depth=3, output_dir="/tmp/uidisco_qa"))
    finally:
        httpd.shutdown()
    analysis = analyze_crawl(crawl)
    return crawl, analysis, classify_analysis(analysis)


def test_smoke_scenario_per_page(context):
    crawl, analysis, semantics = context
    scen = generate_scenarios(crawl, analysis, semantics, None)
    smoke = [s for s in scen if s.type == "smoke"]
    assert len(smoke) == len(crawl.pages)
    # customers page smoke should assert the primary "Create customer" control
    cust = next(s for s in smoke if s.page_url.endswith("customers.html"))
    assert any(st.action == "assert_visible" and st.target == "Create customer"
               for st in cust.steps)


def test_navigation_scenarios_exist(context):
    crawl, analysis, semantics = context
    scen = generate_scenarios(crawl, analysis, semantics, None)
    nav = [s for s in scen if s.type == "navigation"]
    assert nav
    assert all(any(st.action == "assert_url" for st in s.steps) for s in nav)


def test_generated_playwright_compiles(context):
    crawl, analysis, semantics = context
    plan = generate(crawl, analysis, semantics, None, language="py")
    source = build_playwright(plan)
    assert "from playwright.sync_api import" in source
    compile(source, "generated_tests.py", "exec")  # must be valid Python


def test_generated_ts_has_expected_shape(context):
    crawl, analysis, semantics = context
    plan = generate(crawl, analysis, semantics, None, language="ts")
    source = build_playwright(plan)
    assert "@playwright/test" in source and "getByRole" in source


def test_mock_llm_strategy(context):
    crawl, analysis, semantics = context
    plan = generate(crawl, analysis, semantics, None, provider_name="mock")
    assert plan.provider == "mock"
    assert plan.strategy_source == "llm"
    assert plan.strategy.startswith("[mock-generated]")


# --- synthetic page with destructive + form controls ------------------------

def _synthetic():
    url = "http://x/settings"
    els = [
        Element(category="button", tag="button", role="button",
                accessible_name="Delete account", dom_path="button:nth-of-type(1)"),
        Element(category="input", tag="input", role="textbox",
                accessible_name="Email", dom_path="input:nth-of-type(1)"),
    ]
    page = Page(schema_version=SCHEMA_VERSION, engine_version=__version__,
                extracted_at="t", requested_url=url, final_url=url, title="Settings",
                counts={}, headings=[], elements=els)
    crawl = Crawl(schema_version=SCHEMA_VERSION, engine_version=__version__,
                  crawl_id="c", started_at="t", finished_at="t",
                  config=CrawlConfig(start_url=url, max_pages=1, max_depth=1,
                                     strategy="same-domain"),
                  stats=CrawlStats(pages_crawled=1, pages_failed=0, unique_urls=1,
                                   links_discovered=0, runtime_seconds=0.0),
                  pages=[PageNode(url=url, depth=0, page=page)])
    sem = Semantics(schema_version=SCHEMA_VERSION, engine_version=__version__,
                    generated_at="t", start_url=url, labels=[
        SemanticLabel(fingerprint="f1", label="destructive", accessible_name="Delete account",
                      category="button", role="button", page_url=url),
        SemanticLabel(fingerprint="f2", label="form_input", accessible_name="Email",
                      category="input", role="textbox", page_url=url),
    ])
    return crawl, sem


def test_destructive_guard_and_form_scenarios():
    crawl, sem = _synthetic()
    scen = generate_scenarios(crawl, None, sem, None)
    guard = [s for s in scen if s.type == "destructive_guard"]
    form = [s for s in scen if s.type == "form"]
    assert guard and not guard[0].automatable
    assert form and not form[0].automatable
    # the destructive control never becomes an automated click
    plan = generate(crawl, None, sem, None, language="py")
    source = build_playwright(plan)
    assert "Delete account" in source
    assert ".click()" not in source or "Delete account" not in source.split(".click()")[0][-60:]
    assert "SKIP (guard)" in source
    compile(source, "gen.py", "exec")
