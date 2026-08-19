"""Small shared helpers: filesystem slugs, URL normalization, page-graph BFS."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urldefrag, urlparse

# Query params that carry no page-identity meaning (tracking/session noise).
# Stripped only when `dedupe_queries=True` — off by default so existing page
# identity is unchanged unless a caller opts in.
DEFAULT_NOISE_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "ref",
    "sessionid", "session_id", "sid", "phpsessid", "jsessionid",
})


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


def normalize_url(
    url: str,
    *,
    dedupe_queries: bool = False,
    drop_params: frozenset[str] | None = None,
    hash_routes: bool = False,
) -> str:
    """Canonical page identity. Drops the fragment and a trailing slash
    (except root) by default — unchanged from V1 unless a caller opts in to:

    - `dedupe_queries`: strip noise params (`DEFAULT_NOISE_PARAMS` plus any
      `drop_params`) and sort what's left, so query-string variants that
      differ only in tracking/session params collapse to one page identity.
    - `hash_routes`: keep a `#/route`-style fragment as part of identity
      (e.g. `#/orders`), for SPAs that route client-side via the hash. A
      bare anchor fragment (`#section`) is never identity, on or off — it's
      an in-page jump, not a navigation.
    """
    url, frag = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query = parsed.query
    if dedupe_queries and query:
        noise = DEFAULT_NOISE_PARAMS | (drop_params or frozenset())
        pairs = sorted(
            (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if k.lower() not in noise
        )
        query = urlencode(pairs)

    rebuilt = parsed._replace(path=path, query=query)
    result = rebuilt.geturl()
    if hash_routes and frag.startswith("/"):
        result = f"{result}#{frag}"
    return result


def same_site(url: str, root: str) -> bool:
    """True if `url` is on the same host as `root`."""
    return urlparse(url).netloc == urlparse(root).netloc


def resolve_links(
    base_url: str,
    hrefs: list[str],
    root: str,
    *,
    dedupe_queries: bool = False,
    drop_params: frozenset[str] | None = None,
    hash_routes: bool = False,
) -> list[str]:
    """Resolve a page's raw hrefs to absolute, same-site, normalized URLs
    (deduped, order-preserving). file:// and non-http(s) schemes are dropped.

    Bare anchor fragments (`#section`) are always excluded as non-navigational.
    `#/route`-style fragments are included too when `hash_routes=True` (see
    `normalize_url`) — otherwise they're excluded like any other anchor."""
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        low = href.strip().lower()
        if low.startswith(("mailto:", "tel:", "javascript:")):
            continue
        if low.startswith("#") and not (hash_routes and low.startswith("#/") and len(low) > 2):
            continue
        absolute = normalize_url(
            urljoin(base_url, href),
            dedupe_queries=dedupe_queries,
            drop_params=drop_params,
            hash_routes=hash_routes,
        )
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
