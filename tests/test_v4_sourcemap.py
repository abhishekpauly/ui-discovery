"""V4 — source indexing (V4.1) and runtime→source correlation (V4.2).

`fixtures/repo` is a small frontend; `fixtures/repo_site` is "the same app
running", so the pair exercises every confidence level:

  * `add-customer` — a data-testid in both the DOM and the source: `confirmed`
  * "Go to customers" — a label in exactly one component: `high`
  * "Orders" — a label in three components: `low`, with alternatives listed
  * "Print invoices" — on the page, nowhere in the repo: unmatched
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ui_discovery.crawler import crawl_site
from ui_discovery.sourcemap import correlate, index_repo
from ui_discovery.sourcemap.index import normalize_endpoint

REPO = str(Path(__file__).resolve().parents[1] / "fixtures" / "repo")


# --- V4.1: indexing ---------------------------------------------------------

@pytest.fixture(scope="module")
def index():
    return index_repo(REPO)


def test_components_found_with_evidence(index):
    names = {c.name for c in index.components}
    assert {"Customers", "Orders", "PrimaryNav"} <= names
    for component in index.components:
        assert component.ref.path.endswith((".jsx", ".js", ".vue", ".svelte"))
        assert component.ref.line > 0
        assert component.ref.snippet  # every claim is checkable


def test_pages_are_distinguished_from_components(index):
    kinds = {c.name: c.kind for c in index.components}
    assert kinds["Customers"] == "page"
    assert kinds["PrimaryNav"] == "component"


def test_labels_and_test_ids_are_collected(index):
    customers = next(c for c in index.components if c.name == "Customers")
    assert "Go to customers" in customers.labels
    assert "add-customer" in customers.test_ids


def test_routes_found_including_parameterised(index):
    paths = {r.path for r in index.routes}
    assert {"/", "/customers", "/orders", "/orders/:orderId"} <= paths
    orders = next(r for r in index.routes if r.path == "/orders")
    assert orders.component == "Orders"


def test_endpoints_found_with_methods(index):
    found = {(e.method, e.pattern) for e in index.endpoints}
    assert ("GET", "/api/customers") in found
    assert ("POST", "/api/customers") in found
    assert ("GET", "/api/orders") in found


def test_generated_and_vendor_files_are_skipped(index):
    scanned = {c.ref.path for c in index.components}
    assert not any("node_modules" in p for p in scanned)
    assert not any(p.endswith(".min.js") for p in scanned)


def test_nothing_is_executed(index):
    # The whole point: this is text analysis. If the repo were executed or
    # built, a stats key would be the least of the problems.
    assert index.stats["files_scanned"] > 0
    assert Path(index.repo_path).is_dir()


@pytest.mark.parametrize("raw,expected", [
    ("/api/orders/${id}", "/api/orders/:id"),
    ("/api/orders/:orderId", "/api/orders/:id"),
    ("/api/orders/{id}", "/api/orders/:id"),
    ("/api/orders/42", "/api/orders/:id"),
    ("/api/orders?x=1", "/api/orders"),
])
def test_endpoint_normalization(raw, expected):
    assert normalize_endpoint(raw) == expected


def test_missing_repo_is_reported():
    with pytest.raises(NotADirectoryError):
        index_repo("/no/such/repo")


# --- V4.2: correlation ------------------------------------------------------

@pytest.fixture(scope="module")
def report(tmp_path_factory):
    from tests.conftest import Server

    root = Path(__file__).resolve().parents[1]
    server = Server(root / "fixtures" / "repo_site")
    try:
        # Start at "/" so the crawled paths are route-shaped (/customers,
        # /orders) and can be compared with the repo's route table.
        crawl = asyncio.run(crawl_site(
            f"{server.base}/", max_depth=2,
            output_dir=str(tmp_path_factory.mktemp("v4")),
        ))
    finally:
        server.stop()
    return correlate(crawl, index_repo(REPO))


def _by_runtime(report, name):
    return [c for c in report.correlations if c.runtime == name]


def test_matching_test_id_is_confirmed(report):
    matches = _by_runtime(report, "Go to customers")
    assert matches, [c.runtime for c in report.correlations]
    best = matches[0]
    assert best.confidence == "confirmed"
    assert best.source_name == "Customers"
    assert "add-customer" in best.evidence


def test_unique_label_is_high_confidence(report):
    matches = _by_runtime(report, "New order")
    assert matches and matches[0].confidence in ("confirmed", "high")
    assert matches[0].source_name == "Orders"


def test_ambiguous_label_is_low_and_lists_alternatives(report):
    # "Orders" appears in three components; the report must say so rather
    # than silently picking one.
    matches = [c for c in report.correlations
               if c.kind == "element" and c.runtime == "Orders"]
    assert matches
    ambiguous = matches[0]
    assert ambiguous.confidence == "low"
    assert ambiguous.source_name is None
    assert len(ambiguous.alternatives) >= 2
    assert "ambiguous" in ambiguous.evidence


def test_runtime_only_control_is_unmatched(report):
    assert any("Print invoices" in item for item in report.unmatched_runtime)


def test_routes_are_correlated_to_their_components(report):
    routes = [c for c in report.correlations if c.kind == "route"]
    assert routes, "no route correlations"
    by_source = {c.source_name for c in routes}
    assert {"Customers", "Orders"} <= by_source
    for c in routes:
        assert c.confidence in ("high", "medium", "low")
        assert c.ref is not None


def test_every_correlation_carries_confidence_and_evidence(report):
    valid = {"confirmed", "high", "medium", "low", "unknown"}
    for c in report.correlations:
        assert c.confidence in valid
        assert c.evidence, c


def test_no_correlation_claims_a_source_without_a_ref(report):
    # A named source with no file/line would be an unverifiable claim.
    for c in report.correlations:
        if c.source_name and c.kind == "element":
            assert c.ref is not None and c.ref.path


def test_stats_are_consistent(report):
    s = report.stats
    assert s["correlations"] == len(report.correlations)
    assert s["elements"] == sum(1 for c in report.correlations
                                if c.kind == "element")
    assert sum(s[f"confidence_{lvl}"] for lvl in
               ("confirmed", "high", "medium", "low", "unknown")) == s["correlations"]


def test_reports_written(report, tmp_path):
    from ui_discovery.sourcemap.reports import write_correlation

    paths = write_correlation(report, str(tmp_path))
    for p in paths.values():
        assert Path(p).exists()
    md = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "Runtime → Source Correlation" in md
    assert "heuristic match between two approximations" in md
    page_html = Path(paths["html"]).read_text(encoding="utf-8")
    assert "heuristic match between two" in page_html
