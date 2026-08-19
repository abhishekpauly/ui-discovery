"""V4.1 — build a `SourceIndex` from a frontend repo, statically.

The repo is read as text. Nothing is executed, no dependencies are installed,
no build runs. That is a safety property as much as a design one: pointing
this at an unfamiliar repo cannot do anything.

Detection is by shape, not by framework:

* **components** — a source file under a component-ish path that exports a
  symbol starting with a capital letter. That convention holds across React,
  Vue SFCs, Svelte and plain JS module patterns.
* **routes** — string literals sitting next to a `path:`/`route:` key, or in
  a `<Route path=...>`-shaped element.
* **endpoints** — URL-looking string literals passed to `fetch`, `axios`, or
  an `api.get(...)`-style client call.

Everything found carries a `SourceRef` (file + line + the line itself), so no
claim later in V4.2 is unverifiable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .. import SCHEMA_VERSION, __version__
from ..models import (
    SourceComponent,
    SourceEndpoint,
    SourceIndex,
    SourceRef,
    SourceRoute,
)

SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".mjs", ".cjs"}

# Directories that never contain first-party source.
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "out", "coverage", ".next",
    ".nuxt", ".svelte-kit", "__pycache__", ".venv", "venv", "vendor",
    ".cache", "public", "static",
}

MAX_FILE_BYTES = 512_000  # skip bundles/minified blobs

_EXPORTED = re.compile(
    r"export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var)\s+([A-Z][A-Za-z0-9_]*)"
)
_DEFAULT_NAMED = re.compile(r"export\s+default\s+([A-Z][A-Za-z0-9_]*)\s*[;\n]")
_ROUTE_KEY = re.compile(r"""["']?(?:path|route)["']?\s*[:=]\s*["'`]([^"'`]+)["'`]""")
_ROUTE_JSX = re.compile(r"""<Route[^>]*\bpath\s*=\s*["'{]+\s*["'`]?([^"'`}\s]+)""")
_ROUTE_COMPONENT = re.compile(
    r"""(?:component|element)\s*[:=]\s*[{<]?\s*([A-Z][A-Za-z0-9_]*)"""
)
_FETCH = re.compile(
    r"""(?:fetch|axios(?:\.\w+)?|\bapi\.\w+|\bhttp\.\w+|request)\s*\(\s*["'`]([^"'`]+)["'`]"""
)
_METHOD_HINT = re.compile(r"""\b(?:axios|api|http)\.(get|post|put|patch|delete)\b""", re.I)
_METHOD_OPT = re.compile(r"""method\s*:\s*["'](\w+)["']""", re.I)
_URLISH = re.compile(r"^(?:/|https?://)")

# Literal strings worth remembering as candidate UI labels.
_LABEL_ATTR = re.compile(
    r"""(?:aria-label|title|label|placeholder|alt)\s*=\s*["'{]+\s*["'`]?([^"'`}<>]{2,60})"""
)
_JSX_TEXT = re.compile(r">\s*([A-Z][A-Za-z0-9 ,'&./-]{2,48})\s*<")
_TEST_ID = re.compile(
    r"""(?:data-testid|data-test-id|data-cy|testID)\s*=\s*["'{]+\s*["'`]?([^"'`}\s<>]+)"""
)

_COMPONENT_HINT = re.compile(
    r"(^|/)(components?|views?|pages?|screens?|containers?|routes?|widgets?)(/|$)",
    re.I,
)


def _iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name.endswith((".min.js", ".bundle.js", ".d.ts")):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def _read(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _ref(root: Path, path: Path, line_no: int, line: str) -> SourceRef:
    return SourceRef(
        path=path.relative_to(root).as_posix(),
        line=line_no,
        snippet=line.strip()[:200],
    )


def _dedupe(values: Iterable[str], limit: int = 40) -> list[str]:
    seen: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return seen


def normalize_endpoint(url: str) -> str:
    """Collapse a source URL to a shape comparable with observed traffic:
    template placeholders and id-ish segments both become `:id`."""
    url = re.sub(r"\$\{[^}]*\}", ":id", url)          # `/orders/${id}`
    url = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", ":id", url)  # `/orders/:orderId`
    url = re.sub(r"\{[^}]*\}", ":id", url)             # `/orders/{id}`
    url = re.sub(r"/\d+(?=/|$)", "/:id", url)          # `/orders/42`
    return url.split("?", 1)[0].rstrip("/") or "/"


def _component_name(path: Path, lines: list[str]) -> str | None:
    """The exported symbol, else the filename when it looks like a component."""
    text = "\n".join(lines[:400])
    for pattern in (_EXPORTED, _DEFAULT_NAMED):
        match = pattern.search(text)
        if match:
            return match.group(1)
    stem = path.stem
    if stem[:1].isupper() and stem.lower() not in ("index", "app"):
        return stem
    if path.suffix.lower() in (".vue", ".svelte") and stem:
        return stem[:1].upper() + stem[1:]
    return None


def _kind_for(path: Path) -> str:
    lowered = path.as_posix().lower()
    if re.search(r"(^|/)(pages?|views?|screens?|routes?)(/|$)", lowered):
        return "page"
    if "layout" in lowered:
        return "layout"
    return "component"


def _scan_component(root: Path, path: Path, lines: list[str]) -> SourceComponent | None:
    name = _component_name(path, lines)
    if not name:
        return None
    if not (_COMPONENT_HINT.search(path.as_posix()) or path.suffix.lower()
            in (".vue", ".svelte") or name[:1].isupper()):
        return None

    labels: list[str] = []
    test_ids: list[str] = []
    for line in lines:
        labels.extend(_LABEL_ATTR.findall(line))
        labels.extend(_JSX_TEXT.findall(line))
        test_ids.extend(_TEST_ID.findall(line))

    first_line = next((i for i, ln in enumerate(lines, 1) if name in ln), 1)
    return SourceComponent(
        name=name,
        kind=_kind_for(path),
        ref=_ref(root, path, first_line, lines[first_line - 1] if lines else ""),
        labels=_dedupe(labels),
        test_ids=_dedupe(test_ids, limit=20),
    )


def _scan_routes(root: Path, path: Path, lines: list[str]) -> list[SourceRoute]:
    routes: list[SourceRoute] = []
    for line_no, line in enumerate(lines, 1):
        candidates = _ROUTE_JSX.findall(line) + _ROUTE_KEY.findall(line)
        for raw in candidates:
            if not raw.startswith("/") or " " in raw:
                continue
            component = None
            match = _ROUTE_COMPONENT.search(line)
            if match:
                component = match.group(1)
            routes.append(SourceRoute(
                path=raw, component=component,
                ref=_ref(root, path, line_no, line),
            ))
    return routes


def _scan_endpoints(root: Path, path: Path, lines: list[str]) -> list[SourceEndpoint]:
    endpoints: list[SourceEndpoint] = []
    for line_no, line in enumerate(lines, 1):
        for raw in _FETCH.findall(line):
            if not _URLISH.match(raw):
                continue
            method_match = _METHOD_HINT.search(line) or _METHOD_OPT.search(line)
            method = (method_match.group(1) if method_match else "GET").upper()
            endpoints.append(SourceEndpoint(
                method=method, url=raw, pattern=normalize_endpoint(raw),
                ref=_ref(root, path, line_no, line),
            ))
    return endpoints


def index_repo(repo_path: str) -> SourceIndex:
    """Build a `SourceIndex` for the repo at `repo_path`. Read-only."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {repo_path}")

    components: list[SourceComponent] = []
    routes: list[SourceRoute] = []
    endpoints: list[SourceEndpoint] = []
    files_scanned = 0

    for path in _iter_files(root):
        lines = _read(path)
        if not lines:
            continue
        files_scanned += 1
        component = _scan_component(root, path, lines)
        if component:
            components.append(component)
        routes.extend(_scan_routes(root, path, lines))
        endpoints.extend(_scan_endpoints(root, path, lines))

    # Collapse duplicate endpoints by (method, pattern), keeping first evidence.
    unique: dict[tuple[str, str], SourceEndpoint] = {}
    for endpoint in endpoints:
        unique.setdefault((endpoint.method, endpoint.pattern), endpoint)
    endpoints = list(unique.values())

    return SourceIndex(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        indexed_at=datetime.now(timezone.utc).isoformat(),
        repo_path=str(root),
        stats={
            "files_scanned": files_scanned,
            "components": len(components),
            "routes": len(routes),
            "endpoints": len(endpoints),
            "labels": sum(len(c.labels) for c in components),
            "test_ids": sum(len(c.test_ids) for c in components),
        },
        components=components,
        routes=routes,
        endpoints=endpoints,
    )
