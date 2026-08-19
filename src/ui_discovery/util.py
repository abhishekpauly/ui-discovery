"""Small shared helpers: filesystem slugs, URL normalization, page-graph BFS."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse


def slug_for(url: str) -> str:
    """A filesystem-safe folder/file name derived from a URL."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        stem = Path(parsed.path).stem or "page"
        base = f"file_{stem}"
    else:
        host = parsed.netloc or "page"
        path = parsed.path.strip("/")
        base = f"{host}_{path}" if path else host
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
    return (slug or "page")[:120]


def normalize_url(url: str) -> str:
    """Canonical page identity for V1: drop the fragment, drop a trailing
    slash (except root). Query string is kept. SPA fragment-routing is out of
    scope for V1 and is handled in V2."""
    url, _frag = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    rebuilt = parsed._replace(path=path)
    return rebuilt.geturl()


def same_site(url: str, root: str) -> bool:
    """True if `url` is on the same host as `root`."""
    return urlparse(url).netloc == urlparse(root).netloc


def resolve_links(base_url: str, hrefs: list[str], root: str) -> list[str]:
    """Resolve a page's raw hrefs to absolute, same-site, normalized URLs
    (deduped, order-preserving). file:// and non-http(s) schemes are dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        low = href.strip().lower()
        if low.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        scheme = urlparse(absolute).scheme
        if scheme not in ("http", "https", "file"):
            continue
        if not same_site(absolute, root):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def bfs_depths(root: str, edges: dict[str, list[str]]) -> dict[str, int]:
    """Depth of each node from `root` over the discovered edge set."""
    depths = {root: 0}
    queue = deque([root])
    while queue:
        node = queue.popleft()
        for nxt in edges.get(node, []):
            if nxt not in depths:
                depths[nxt] = depths[node] + 1
                queue.append(nxt)
    return depths
