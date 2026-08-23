"""M2 — answer "what would you crawl, and why?" without crawling.

Scoping a real portal is guesswork until the run finishes and the budget is
spent. You point the engine at a target, wait forty minutes, and find out that
`exclude` was one pattern too broad or that the budget stopped three modules
short. `S1`'s whole purpose — making scope a decision — was undermined by the
fact that the decision could only be checked by paying for it.

`map` is that check. It **never navigates**: it reads the config, reads what
the product declares about itself via `M1`, applies exactly the gates the
crawler applies, and writes down the verdict for every URL with **the rule that
decided it**.

That last part is the whole point. A verdict without its reason cannot be acted
on, because you cannot tell which line of the config to change. "Out of scope"
is useless; "excluded by `/reports/**`" is a decision.

**On `link` and `deep-nav` sources.** A URL discovered by following a link is,
by definition, something only navigation can know. `map` does not navigate, so
those entries appear only when `--from-crawl` points at a capture that already
did — which is how you get "everything the last crawl found, re-judged against
the config you are about to use" without paying for the crawl twice.

There is deliberately **no ranking and no scoring**. `--search` is a plain glob
over the path. A ranked list nobody can reproduce is worse than an unranked one.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import SCHEMA_VERSION, __version__
from .cliconfig import add_config_argument, load_or_exit, resolve_output_dir
from .config import Scope
from .discovery import read_sitemaps
from .models import Crawl, MappedUrl, UrlMap
from .util import normalize_url, path_matches, same_site, slug_for

# The order sources are reported in: what the config states, then what the
# product declares, then what navigation found. Stable, so two runs of the
# same map are byte-identical.
SOURCE_ORDER = ("seed", "module", "sitemap", "link", "deep-nav")


def decide(url: str, scope: Scope, start_url: str) -> tuple[bool, str]:
    """Is this URL in scope, and **which rule said so**?

    Applies the gates in the crawler's own order — same-site first, then
    exclude, then include — so a map cannot disagree with the crawl it
    predicts. Each branch names the specific pattern rather than the category,
    because "excluded" does not tell you which line to edit.
    """
    policy = scope.scope.subdomains
    hosts = tuple(scope.scope.subdomain_hosts)
    if not same_site(url, start_url, policy, hosts):
        return False, f"subdomain-policy:{policy}"

    for pattern in scope.scope.exclude or ():
        if path_matches(url, pattern):
            return False, f"exclude:{pattern}"

    include = scope.scope.include or []
    if not include:
        return True, "default:everything-not-excluded"
    for pattern in include:
        if path_matches(url, pattern):
            return True, f"include:{pattern}"
    return False, "include:no-pattern-matched"


def _crawl_sources(crawl: Crawl) -> dict[str, tuple[str, Optional[int]]]:
    """URLs a previous capture discovered, with how and how deep."""
    found: dict[str, tuple[str, Optional[int]]] = {}
    for node in crawl.pages:
        found.setdefault(node.url, ("link", node.depth))
    for edge in crawl.navigation:
        target = edge.get("to")
        if not target:
            continue
        source = "deep-nav" if edge.get("control") == "deep-nav" else "link"
        found.setdefault(target, (source, None))
    for failure in crawl.failures:
        found.setdefault(failure.url, ("link", failure.depth))
    return found


def build_map(
    start_url: str,
    scope: Scope,
    *,
    search: Optional[str] = None,
    crawl: Optional[Crawl] = None,
    read_sitemap: bool = True,
    max_pages: Optional[int] = None,
) -> UrlMap:
    """The URL surface, judged. Pure apart from the sitemap fetch.

    `search` narrows which entries are *listed*; it never changes a verdict.
    A filter that also re-judged would make two maps of one config disagree,
    which is the opposite of what this artifact is for.
    """
    normalize = _normalizer(scope)
    root = normalize(start_url)
    candidates: dict[str, tuple[str, Optional[int]]] = {root: ("seed", 0)}

    for module in scope.modules:
        if module.start_url:
            candidates.setdefault(normalize(module.start_url), ("module", None))

    warnings: list[str] = []
    if read_sitemap and scope.discovery.sitemap != "skip":
        found = read_sitemaps(
            start_url,
            mode=scope.discovery.sitemap,
            include=scope.scope.include,
            exclude=scope.scope.exclude,
            subdomains=scope.scope.subdomains,
            subdomain_hosts=tuple(scope.scope.subdomain_hosts),
            timeout=scope.discovery.sitemap_timeout,
            dedupe_queries=scope.identity.dedupe_queries,
            drop_params=frozenset(scope.identity.drop_params or ()),
        )
        warnings.extend(found.warnings)
        for url in found.urls:
            candidates.setdefault(normalize(url), ("sitemap", None))
        # A URL the sitemap listed and the scope declined belongs on the map
        # *with its verdict*. Dropping it here would hide the most useful
        # thing a map can show: the rule that is costing you a module.
        for dropped in found.dropped:
            candidates.setdefault(normalize(dropped["url"]), ("sitemap", None))

    if crawl is not None:
        for url, (source, depth) in _crawl_sources(crawl).items():
            candidates.setdefault(normalize(url), (source, depth))

    entries: list[MappedUrl] = []
    for url, (source, depth) in candidates.items():
        in_scope, rule = decide(url, scope, root)
        entries.append(MappedUrl(url=url, source=source, in_scope=in_scope,
                                 decided_by=rule, depth=depth))

    # The budget is applied *after* scope, to the in-scope set, in the order a
    # crawl would reach them — so "you will run out before Reports" is a thing
    # the map can say.
    # The flag wins over the config, exactly as it does on a real run. A
    # preview that ignored `--max-pages` would predict a different crawl from
    # the one the operator is about to start, which is the one thing this
    # artifact must never do.
    max_pages = scope.budget.max_pages if max_pages is None else max_pages
    in_scope_seen = 0
    for entry in sorted(entries, key=_ordering):
        if not entry.in_scope:
            continue
        in_scope_seen += 1
        if in_scope_seen > max_pages:
            entry.in_scope = False
            entry.decided_by = f"budget:max_pages={max_pages}"

    if search:
        entries = [e for e in entries if path_matches(e.url, search)]

    entries.sort(key=_ordering)
    kept = [e for e in entries if e.in_scope]

    # M3: which declared modules this config cannot reach, **by name**. A
    # budget verdict of "12 of 30" does not tell an operator that Reports is
    # the module they are about to lose, and the module names are the only
    # part of a scope config written in their language rather than the
    # engine's.
    verdict = {e.url: (e.in_scope, e.decided_by) for e in entries}
    unreachable = []
    for module in scope.modules:
        if not module.start_url:
            continue
        module_url = normalize(module.start_url)
        reachable, rule = verdict.get(module_url, (None, "not-on-the-map"))
        if reachable is False:
            unreachable.append({"module": module.name, "url": module_url,
                                "decided_by": rule})
    return UrlMap(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        start_url=root,
        search=search,
        from_crawl=(crawl.crawl_id if crawl is not None else None),
        entries=entries,
        stats={
            "total": len(entries),
            "in_scope": len(kept),
            "out_of_scope": len(entries) - len(kept),
            "by_source": _counted(e.source for e in entries),
            "by_rule": _counted(e.decided_by for e in entries if not e.in_scope),
            "max_pages": max_pages,
            "modules_unreachable": unreachable,
        },
        warnings=warnings,
    )


def _normalizer(scope: Scope):
    drop = frozenset(scope.identity.drop_params or ())

    def normalize(url: str) -> str:
        return normalize_url(url, dedupe_queries=scope.identity.dedupe_queries,
                             drop_params=drop,
                             hash_routes=scope.identity.hash_routes)
    return normalize


def _ordering(entry: MappedUrl) -> tuple:
    """Deterministic and stable: source first, then URL. Two runs of the same
    map must be byte-identical, or nobody can diff one against another."""
    try:
        rank = SOURCE_ORDER.index(entry.source)
    except ValueError:
        rank = len(SOURCE_ORDER)
    return (rank, entry.url)


def _counted(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def write_map(url_map: UrlMap, output_dir: str) -> dict[str, str]:
    """`map.json` + `urls.txt`. The second is deliberately plain: it is what
    `H10`'s `--from` consumes, so a map can be filtered by hand and handed
    straight back to the engine."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": str(out / "map.json"), "urls": str(out / "urls.txt")}
    Path(paths["json"]).write_text(
        json.dumps(url_map.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8")
    Path(paths["urls"]).write_text(
        "".join(f"{e.url}\n" for e in url_map.entries if e.in_scope),
        encoding="utf-8")
    return paths


def render_map(url_map: UrlMap) -> str:
    """The map as a person reads it — verdict first, then the reason."""
    stats = url_map.stats
    lines = [
        f"[INFO] Mapped {stats['total']} URL(s): "
        f"{stats['in_scope']} in scope, {stats['out_of_scope']} not.",
    ]
    if url_map.search:
        lines.append(f"[INFO] Narrowed by --search {url_map.search!r} "
                     f"(a filter on what is listed, never on a verdict).")
    for source, count in stats["by_source"].items():
        lines.append(f"[INFO]   from {source}: {count}")
    for missed in stats.get("modules_unreachable") or ():
        lines.append(f"[WARN] Module {missed['module']!r} is not reachable "
                     f"with this config ({missed['decided_by']}) — "
                     f"{missed['url']}")
    if stats["by_rule"]:
        lines.append("[INFO] Excluded by:")
        for rule, count in stats["by_rule"].items():
            lines.append(f"[INFO]   {rule}: {count}")
    for warning in url_map.warnings:
        lines.append(f"[WARN] {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_discovery.map",
        description="M2: what a crawl would do, and why — without navigating.")
    add_config_argument(parser)
    parser.add_argument("url", nargs="?", default=None,
                        help="Start URL. Optional if the config sets start_url.")
    parser.add_argument("--output", default=None, help="Output directory.")
    parser.add_argument(
        "--search", default=None, metavar="GLOB",
        help="Glob over the URL path (`/admin/*`, `/reports/**`). Narrows "
             "what is listed; never changes a verdict. Deterministic — there "
             "is no ranking and no scoring.")
    parser.add_argument(
        "--from-crawl", default=None, metavar="DIR",
        help="Fold in the URLs a previous capture discovered, re-judged "
             "against this config. `map` never navigates, so this is the only "
             "way `link` and `deep-nav` sources can appear.")
    parser.add_argument(
        "--no-sitemap", action="store_true", default=False,
        help="Do not read robots.txt / sitemap.xml. Makes the command "
             "entirely offline.")
    args = parser.parse_args(argv)

    scope = load_or_exit(args.config, getattr(args, "profile", None))
    try:
        url = scope.resolve_start_url(args.url)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    crawl = None
    if args.from_crawl:
        crawl_path = Path(args.from_crawl)
        if crawl_path.is_dir():
            crawl_path = crawl_path / "crawl.json"
        try:
            crawl = Crawl.model_validate_json(
                crawl_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[ERROR] Could not read {crawl_path}: {exc}", file=sys.stderr)
            return 1

    url_map = build_map(url, scope, search=args.search, crawl=crawl,
                        read_sitemap=not args.no_sitemap)
    out_dir = Path(resolve_output_dir(scope, args.output, slug_for(url)))
    paths = write_map(url_map, str(out_dir))

    print(render_map(url_map))
    print(f"[INFO] Wrote {paths['json']}")
    print(f"[INFO] Wrote {paths['urls']} "
          f"(feed it back with `crawl --from urls.txt`)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
