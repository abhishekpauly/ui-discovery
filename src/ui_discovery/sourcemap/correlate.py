"""V4.2 — link what was observed at runtime to what the index found in source.

**Never present inference as certainty.** These are heuristics over two
approximations (a static scan and a runtime capture), so every link carries a
confidence level and the evidence that produced it:

| confidence | means |
|---|---|
| `confirmed` | An identifier both sides agree on — a `data-testid` present in the DOM and in the source |
| `high`      | An exact, unique match on an unusual signal (accessible name found in exactly one component; a route pattern equal to the observed path) |
| `medium`    | An exact match that is not unique, or a normalized-pattern match |
| `low`       | A weak or ambiguous match — reported *with* its alternatives, never silently resolved |
| `unknown`   | No candidate found |

An ambiguous match is never promoted to a guess. When several components
could own a control, the correlation says `low` and lists them all, because
"we don't know which of these three" is a useful answer and "it's this one"
would be a fabricated one.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .. import SCHEMA_VERSION, __version__
from ..models import (
    Correlation,
    CorrelationReport,
    Crawl,
    SourceComponent,
    SourceIndex,
)
from .index import normalize_endpoint

_WS = re.compile(r"\s+")


def _norm(text: Optional[str]) -> str:
    return _WS.sub(" ", (text or "")).strip().lower()


def _route_to_regex(route: str) -> re.Pattern:
    """`/orders/:id` -> a pattern matching `/orders/42`."""
    parts = []
    for chunk in re.split(r"(:[A-Za-z_][A-Za-z0-9_]*|\*)", route):
        if chunk.startswith(":"):
            parts.append(r"[^/]+")
        elif chunk == "*":
            parts.append(r".*")
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts).rstrip("/") + "/?$")


# --- elements ----------------------------------------------------------------

def _index_labels(index: SourceIndex) -> dict[str, list[SourceComponent]]:
    by_label: dict[str, list[SourceComponent]] = {}
    for component in index.components:
        for label in component.labels:
            by_label.setdefault(_norm(label), []).append(component)
    return by_label


def _index_test_ids(index: SourceIndex) -> dict[str, list[SourceComponent]]:
    by_id: dict[str, list[SourceComponent]] = {}
    for component in index.components:
        for test_id in component.test_ids:
            by_id.setdefault(test_id.strip(), []).append(component)
    return by_id


def _correlate_element(
    el, page_url: str,
    by_label: dict[str, list[SourceComponent]],
    by_test_id: dict[str, list[SourceComponent]],
) -> Optional[Correlation]:
    name = el.accessible_name
    test_id = (el.attributes or {}).get("data-testid")

    # Strongest signal: an identifier both sides chose deliberately.
    if test_id and test_id in by_test_id:
        owners = by_test_id[test_id]
        return Correlation(
            kind="element", runtime=name or test_id, runtime_page=page_url,
            source_name=owners[0].name, ref=owners[0].ref,
            confidence="confirmed" if len(owners) == 1 else "medium",
            evidence=f"data-testid {test_id!r} appears in "
                     f"{owners[0].ref.path}:{owners[0].ref.line}",
            alternatives=[c.name for c in owners[1:]],
        )

    if not name:
        return None

    owners = by_label.get(_norm(name), [])
    if not owners:
        return None
    if len(owners) == 1:
        component = owners[0]
        return Correlation(
            kind="element", runtime=name, runtime_page=page_url,
            source_name=component.name, ref=component.ref,
            confidence="high",
            evidence=f"label {name!r} found in exactly one component "
                     f"({component.ref.path}:{component.ref.line})",
        )
    # Several components use this label. Say so; do not pick one.
    return Correlation(
        kind="element", runtime=name, runtime_page=page_url,
        source_name=None, ref=None, confidence="low",
        evidence=f"label {name!r} appears in {len(owners)} components — "
                 f"ambiguous, not resolved",
        alternatives=[c.name for c in owners],
    )


# --- routes ------------------------------------------------------------------

def _correlate_route(url: str, index: SourceIndex) -> Optional[Correlation]:
    from urllib.parse import urlparse

    path = (urlparse(url).path or "/").rstrip("/") or "/"

    exact = [r for r in index.routes if (r.path.rstrip("/") or "/") == path]
    if exact:
        route = exact[0]
        return Correlation(
            kind="route", runtime=url, runtime_page=url,
            source_name=route.component or route.path, ref=route.ref,
            confidence="high" if len(exact) == 1 else "medium",
            evidence=f"route {route.path!r} matches the observed path exactly",
            alternatives=[r.path for r in exact[1:]],
        )

    # Parameterised routes: /orders/:id against /orders/42.
    matches = [r for r in index.routes
               if ":" in r.path and _route_to_regex(r.path).match(path)]
    if matches:
        route = matches[0]
        return Correlation(
            kind="route", runtime=url, runtime_page=url,
            source_name=route.component or route.path, ref=route.ref,
            confidence="medium" if len(matches) == 1 else "low",
            evidence=f"route pattern {route.path!r} matches {path!r}",
            alternatives=[r.path for r in matches[1:]],
        )
    return None


# --- endpoints ---------------------------------------------------------------

def _observed_endpoints(crawl: Crawl) -> dict[tuple[str, str], str]:
    """(method, normalized pattern) -> an example observed URL. Only present
    when the crawl ran with --probe."""
    seen: dict[tuple[str, str], str] = {}
    for node in crawl.pages:
        if not node.probe:
            continue
        for request in node.probe.network:
            if not request.is_api:
                continue
            key = (request.method.upper(), normalize_endpoint(request.url))
            seen.setdefault(key, request.url)
    return seen


def _correlate_endpoint(
    method: str, pattern: str, example: str, index: SourceIndex,
) -> Optional[Correlation]:
    def tail(value: str) -> str:
        return value.split("://", 1)[-1].split("/", 1)[-1] if "://" in value else value

    candidates = [
        e for e in index.endpoints
        if tail(e.pattern).rstrip("/") == tail(pattern).rstrip("/")
    ]
    if not candidates:
        return None
    exact_method = [e for e in candidates if e.method.upper() == method]
    chosen = (exact_method or candidates)[0]
    if exact_method and len(exact_method) == 1:
        confidence, note = "high", "method and path both match"
    elif exact_method:
        confidence, note = "medium", "method and path match, several call sites"
    else:
        confidence, note = "low", f"path matches but source method is {chosen.method}"
    return Correlation(
        kind="endpoint", runtime=f"{method} {example}",
        source_name=chosen.url, ref=chosen.ref,
        confidence=confidence,
        evidence=f"{note} ({chosen.ref.path}:{chosen.ref.line})"
                 if chosen.ref else note,
        alternatives=[e.url for e in candidates[1:]],
    )


# --- report ------------------------------------------------------------------

def correlate(crawl: Crawl, index: SourceIndex) -> CorrelationReport:
    """Link a crawl to a source index. Pure and deterministic."""
    by_label = _index_labels(index)
    by_test_id = _index_test_ids(index)

    correlations: list[Correlation] = []
    unmatched_runtime: list[str] = []
    seen_elements: set[tuple[str, str]] = set()

    for node in crawl.pages:
        route = _correlate_route(node.url, index)
        if route:
            correlations.append(route)
        elif index.routes:
            unmatched_runtime.append(f"route {node.url}")

        for el in node.page.elements:
            if el.category not in ("button", "link", "input", "select", "nav"):
                continue
            key = (node.url, _norm(el.accessible_name) or el.dom_path)
            if key in seen_elements:
                continue
            seen_elements.add(key)
            link = _correlate_element(el, node.url, by_label, by_test_id)
            if link:
                correlations.append(link)
            elif el.accessible_name:
                unmatched_runtime.append(f"control “{el.accessible_name}”")

    for (method, pattern), example in sorted(_observed_endpoints(crawl).items()):
        link = _correlate_endpoint(method, pattern, example, index)
        if link:
            correlations.append(link)
        else:
            unmatched_runtime.append(f"endpoint {method} {pattern}")

    matched_components = {c.source_name for c in correlations if c.source_name}
    unmatched_source = sorted(
        c.name for c in index.components if c.name not in matched_components
    )

    by_confidence = {level: 0 for level in
                     ("confirmed", "high", "medium", "low", "unknown")}
    for link in correlations:
        by_confidence[link.confidence] = by_confidence.get(link.confidence, 0) + 1

    return CorrelationReport(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo_path=index.repo_path,
        source_crawl_id=crawl.crawl_id,
        stats={
            "correlations": len(correlations),
            "elements": sum(1 for c in correlations if c.kind == "element"),
            "routes": sum(1 for c in correlations if c.kind == "route"),
            "endpoints": sum(1 for c in correlations if c.kind == "endpoint"),
            "unmatched_runtime": len(unmatched_runtime),
            "unmatched_source": len(unmatched_source),
            **{f"confidence_{k}": v for k, v in by_confidence.items()},
        },
        correlations=correlations,
        unmatched_runtime=sorted(set(unmatched_runtime))[:200],
        unmatched_source=unmatched_source[:200],
    )
