"""M1 — read the target's own declaration of its URLs.

The engine finds URLs one way: by walking what it has already rendered, plus
`D4`'s deep-nav clicking. That is thorough, slow, and blind to any module the
landing page never links to. Most products publish their own answer at
`/sitemap.xml`, and the engine has never read it.

The obvious gain is faster, wider coverage. The one that matters more is that a
sitemap is how you find out what a product *claims* to contain — the input
`M2`'s map needs to say what a crawl would do, and `M4` needs to spot a screen
that works by URL and that nothing links to.

Three rules shape this module.

**A sitemap is a suggestion, never an authorization.** Everything it lists goes
through the same gate a discovered link does: `H6`'s same-site policy, then the
config's `include`/`exclude`. A product listing a partner's domain in its own
sitemap does not thereby grant permission to crawl it.

**Nothing here raises.** Real products ship broken sitemaps, 404s, and XML with
a stray ampersand in it. Each of those is a warning and an empty list. Refusing
to start a capture because an optional file was malformed would be the worse
failure by a wide margin.

**Everything fetched is reported.** These requests are made with `urllib`
rather than through the browser, so `G7`'s egress ledger would not otherwise
see them — and a ledger that missed the engine's own traffic would be worth
very little. `SitemapResult.fetched` is how the caller keeps that honest.
"""

from __future__ import annotations

import gzip
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from . import __version__
from .util import SAME_HOST, normalize_url, same_site, url_in_scope

# `include` reads what is there; `skip` reproduces the crawl exactly as it was
# before this module existed; `only` crawls the sitemap and follows nothing,
# which is the fast survey.
SITEMAP_MODES = ("include", "skip", "only")

# One level of `<sitemapindex>` is followed. Deeper nesting is legal and
# vanishingly rare, and an unbounded follow is a way to be walked into a very
# large fetch by a file the engine does not control.
MAX_INDEX_DEPTH = 1

# The sitemap protocol's own ceiling is 50MB uncompressed. Past that it is not
# a sitemap worth reading.
MAX_BYTES = 50 * 1024 * 1024

USER_AGENT = f"ui-discovery/{__version__} (sitemap reader)"


@dataclass
class SitemapResult:
    """What the target said about itself, and what was done with it."""

    # In-scope URLs, deduped, in the order first seen.
    urls: list[str] = field(default_factory=list)
    # What the sitemap listed and the scope rules declined, each with why.
    # A dropped URL is a decision, and `M2` renders the reason.
    dropped: list[dict] = field(default_factory=list)
    # Every URL this module requested, so `G7`'s ledger can account for
    # traffic the browser never made.
    fetched: list[str] = field(default_factory=list)
    # The sitemap documents successfully read.
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.urls)

    def summary(self) -> dict:
        return {
            "urls": len(self.urls),
            "dropped": len(self.dropped),
            "sources": list(self.sources),
            "warnings": list(self.warnings),
        }


def sitemap_urls_from_robots(body: bytes) -> list[str]:
    """The `Sitemap:` directives in a robots.txt, in order.

    Deliberately only this directive. `X5` owns whether the engine honours
    robots' *rules*; reading the line that says where the sitemap lives is a
    different question from obeying a `Disallow`, and conflating them would
    make one imply the other.
    """
    found: list[str] = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        name, sep, value = line.partition(":")
        if not sep or name.strip().lower() != "sitemap":
            continue
        url = value.strip()
        if url:
            found.append(url)
    return found


def locations_in(body: bytes) -> tuple[list[str], bool]:
    """Every `<loc>` in a sitemap document, and whether it is an index.

    Namespace-agnostic on purpose: the sitemap namespace is declared correctly
    by most products and slightly wrong by enough of them that matching on the
    local tag name is the only thing that reads them all. Raises
    `ElementTree.ParseError` — the caller turns that into a warning.
    """
    root = ElementTree.fromstring(body)
    is_index = root.tag.rsplit("}", 1)[-1] == "sitemapindex"
    found = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "loc":
            continue
        text = (element.text or "").strip()
        if text:
            found.append(text)
    return found, is_index


def _fetch(url: str, timeout: float, result: SitemapResult,
           opener=None) -> bytes | None:
    """GET `url`, transparently gunzipping. `None` on any failure, with a
    warning recorded — never an exception."""
    result.fetched.append(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result.warnings.append(f"{url}: not read ({type(exc).__name__})")
        return None

    if len(body) > MAX_BYTES:
        result.warnings.append(f"{url}: larger than {MAX_BYTES} bytes, ignored")
        return None
    # `.gz` by extension, by header, or by magic number. Some servers
    # decompress transparently and some mislabel, so all three are checked
    # rather than any one being trusted.
    if url.endswith(".gz") or "gzip" in encoding or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            pass  # it claimed gzip and was not; try it as-is
    return body


def read_sitemaps(
    start_url: str,
    *,
    mode: str = "include",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    subdomains: str = SAME_HOST,
    subdomain_hosts: tuple[str, ...] = (),
    timeout: float = 10.0,
    dedupe_queries: bool = False,
    drop_params: frozenset[str] | None = None,
    opener=None,
) -> SitemapResult:
    """Seed URLs from the target's `robots.txt` / `sitemap.xml`.

    Returns in-scope URLs plus a record of what was dropped and why. The
    caller feeds `urls` into the crawler's **existing** `seeds` option rather
    than adding a second seeding path, so from that point on a sitemap URL and
    a hand-written seed are the same kind of thing.

    `opener` exists for tests, which serve fixtures over a local port; it is
    the one seam that keeps the suite from needing a public sitemap.
    """
    result = SitemapResult()
    if mode == "skip":
        return result
    if mode not in SITEMAP_MODES:
        result.warnings.append(
            f"discovery.sitemap: unknown mode {mode!r}; nothing read")
        return result

    robots_url = urljoin(start_url, "/robots.txt")
    robots = _fetch(robots_url, timeout, result, opener)
    # Resolved against robots.txt's own location. The spec says a `Sitemap:`
    # directive is absolute; enough products write `/sitemap.xml` that
    # refusing to resolve one is a self-inflicted blind spot.
    candidates = [urljoin(robots_url, u)
                  for u in (sitemap_urls_from_robots(robots) if robots else [])]
    if not candidates:
        # No directive, or no robots.txt at all. The conventional location is
        # worth one request before giving up.
        candidates = [urljoin(start_url, "/sitemap.xml")]

    seen_documents: set[str] = set()
    seen_urls: set[str] = set()
    queue = [(url, 0) for url in candidates]

    while queue:
        document_url, depth = queue.pop(0)
        if document_url in seen_documents:
            continue
        seen_documents.add(document_url)

        # A sitemap naming another host is the clearest possible case of a
        # file the engine does not control asking it to leave the target.
        if not same_site(document_url, start_url, subdomains, subdomain_hosts):
            result.warnings.append(
                f"{document_url}: sitemap on another host, not fetched")
            continue

        body = _fetch(document_url, timeout, result, opener)
        if body is None:
            continue
        try:
            locations, is_index = locations_in(body)
        except ElementTree.ParseError as exc:
            result.warnings.append(f"{document_url}: malformed XML ({exc.msg})")
            continue
        result.sources.append(document_url)

        if is_index:
            if depth >= MAX_INDEX_DEPTH:
                result.warnings.append(
                    f"{document_url}: index nested deeper than "
                    f"{MAX_INDEX_DEPTH} level(s), not followed")
                continue
            queue.extend((urljoin(document_url, loc), depth + 1)
                         for loc in locations)
            continue

        for raw in locations:
            # Same tolerance as above, and for the same reason: `<loc>` is
            # specified as absolute and is not always written that way.
            url = normalize_url(urljoin(document_url, raw),
                                dedupe_queries=dedupe_queries,
                                drop_params=drop_params)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if urlparse(url).scheme not in ("http", "https"):
                result.dropped.append({"url": url, "reason": "not-http"})
            elif not same_site(url, start_url, subdomains, subdomain_hosts):
                result.dropped.append({"url": url, "reason": "off-site"})
            elif not url_in_scope(url, include, exclude):
                result.dropped.append({"url": url, "reason": "out-of-scope"})
            else:
                result.urls.append(url)

    return result
