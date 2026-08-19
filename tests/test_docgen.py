"""V5.2 documentation-generation tests — deterministic default + mock prose.

Zero-token: the deterministic assembler needs no provider; MockTextProvider is a
pure offline stand-in. No real LLM is called.
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
from ui_discovery.docgen import generate
from ui_discovery.llm import MockTextProvider, get_text_provider
from ui_discovery.reports import write_documentation
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
                                       max_depth=3, output_dir="/tmp/uidisco_doc"))
    finally:
        httpd.shutdown()
    analysis = analyze_crawl(crawl)
    semantics = classify_analysis(analysis)
    return crawl, analysis, semantics


# --- llm provider seam ------------------------------------------------------

def test_text_provider_none_is_deterministic():
    assert get_text_provider("none") is None


def test_mock_text_provider_completes_offline():
    out = MockTextProvider().complete("Describe the Customers page\nmore text")
    assert out.startswith("[mock-generated]")


# --- deterministic documentation --------------------------------------------

def test_deterministic_doc(context):
    crawl, analysis, semantics = context
    doc = generate(crawl, analysis, semantics, provider_name="none")
    assert doc.provider == "deterministic"
    assert doc.overview_source == "deterministic"
    assert str(len(crawl.pages)) in doc.overview
    assert doc.global_nav and "Home" in doc.global_nav
    assert doc.shared_components  # shared nav/components detected
    assert len(doc.pages) == len(crawl.pages)
    # semantic control grouping present
    customers = next(p for p in doc.pages if p.url.endswith("customers.html"))
    assert "primary_action" in customers.controls  # "Create customer"
    assert any(p.purpose for p in doc.pages)


def test_doc_works_without_analysis_or_semantics(context):
    crawl, _a, _s = context
    doc = generate(crawl, analysis=None, semantics=None, provider_name="none")
    assert len(doc.pages) == len(crawl.pages)
    # falls back to category grouping from the page model
    assert any(p.controls for p in doc.pages)


def test_reports_written(context, tmp_path):
    crawl, analysis, semantics = context
    doc = generate(crawl, analysis, semantics)
    paths = write_documentation(doc, str(tmp_path))
    for p in paths.values():
        assert Path(p).exists()
    md = (tmp_path / "documentation.md").read_text()
    assert "UI Documentation" in md and "Overview" in md


# --- optional mock LLM prose ------------------------------------------------

def test_mock_llm_prose_layers_on_top(context):
    crawl, analysis, semantics = context
    doc = generate(crawl, analysis, semantics, provider_name="mock")
    assert doc.provider == "mock"
    assert doc.overview_source == "llm"
    assert doc.overview.startswith("[mock-generated]")
    assert any(p.purpose_source == "llm" for p in doc.pages)
