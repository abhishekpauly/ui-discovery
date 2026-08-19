"""C1 — deterministic change diff between two snapshots.

    python -m ui_discovery.diff <old> <new>

Given two analyses of the same site, report what changed: pages added or
removed, elements added or removed, controls **renamed**, and components
gained or lost. No LLM, no network — pure comparison of two stored models,
so the same pair of snapshots always produces the same diff.

Rename detection is the payoff of fingerprinting, and it needs two passes
because `fingerprint` is deliberately name-sensitive (see
`analysis/fingerprint.py`):

* Elements identified by a stable `data-testid` / `id` / `name` keep their
  fingerprint when relabelled — so *same fingerprint, different accessible
  name* is a rename (`match="fingerprint"`).
* Elements identified structurally bake the name into the fingerprint, so a
  rename changes it. Those are recovered by pairing the leftover added and
  removed elements on a name-independent structural key
  (`match="structural"`), and only when the pairing is unambiguous.

Anything not confidently paired stays an add and a remove — this never
guesses a rename it cannot evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, TypeVar

from . import SCHEMA_VERSION, __version__
from .analysis.fingerprint import structural_signature
from .models import (
    Analysis,
    Component,
    ComponentChange,
    Diff,
    DiffSide,
    ElementChange,
    ElementFingerprint,
    PageAnalysis,
    PageChange,
)
from .reports import write_diff

T = TypeVar("T")


def _group(items: Iterable[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    out: dict[str, list[T]] = defaultdict(list)
    for item in items:
        out[key(item)].append(item)
    return dict(out)


def _norm_name(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _rename_key(fp: ElementFingerprint) -> str:
    """A deliberately **name-independent** identity for one element, so a
    relabelled control still matches itself across snapshots."""
    return "|".join([
        fp.category,
        fp.role or "",
        fp.landmark or "",
        structural_signature(fp.dom_path),
    ])


def _change(kind: str, page_url: str, fp: ElementFingerprint, **extra) -> ElementChange:
    return ElementChange(
        page_url=page_url,
        kind=kind,
        fingerprint=fp.fingerprint,
        category=fp.category,
        role=fp.role,
        accessible_name=fp.accessible_name,
        landmark=fp.landmark,
        **extra,
    )


def _pair_by_fingerprint(
    page_url: str,
    old_fps: list[ElementFingerprint],
    new_fps: list[ElementFingerprint],
) -> tuple[list[ElementChange], list[ElementFingerprint], list[ElementFingerprint]]:
    """First pass: match on the fingerprint itself. Returns the renames found
    plus whatever stayed unmatched on each side."""
    old_map = _group(old_fps, lambda f: f.fingerprint)
    new_map = _group(new_fps, lambda f: f.fingerprint)

    changes: list[ElementChange] = []
    leftover_old: list[ElementFingerprint] = []
    leftover_new: list[ElementFingerprint] = []

    for key in set(old_map) | set(new_map):
        olds, news = old_map.get(key, []), new_map.get(key, [])
        paired = min(len(olds), len(news))
        for old_fp, new_fp in zip(olds[:paired], news[:paired]):
            # A stable-id element keeps its fingerprint through a relabel, so
            # a differing name here is a rename rather than a coincidence.
            if _norm_name(old_fp.accessible_name) != _norm_name(new_fp.accessible_name):
                changes.append(_change(
                    "renamed", page_url, new_fp,
                    previous_name=old_fp.accessible_name,
                    match="fingerprint",
                ))
        leftover_old.extend(olds[paired:])
        leftover_new.extend(news[paired:])

    return changes, leftover_old, leftover_new


def _pair_by_structure(
    page_url: str,
    leftover_old: list[ElementFingerprint],
    leftover_new: list[ElementFingerprint],
) -> tuple[list[ElementChange], set[int], set[int]]:
    """Second pass: recover structural-strategy renames, whose fingerprint
    moved because the name is part of it. Only unambiguous 1:1 matches are
    accepted — several candidates on either side is not evidence of a rename."""
    old_by_key = _group(leftover_old, _rename_key)
    new_by_key = _group(leftover_new, _rename_key)

    changes: list[ElementChange] = []
    matched_old: set[int] = set()
    matched_new: set[int] = set()

    for key in set(old_by_key) & set(new_by_key):
        olds, news = old_by_key[key], new_by_key[key]
        if len(olds) != 1 or len(news) != 1:
            continue
        old_fp, new_fp = olds[0], news[0]
        matched_old.add(id(old_fp))
        matched_new.add(id(new_fp))
        if _norm_name(old_fp.accessible_name) == _norm_name(new_fp.accessible_name):
            # Same control, same label — only its identity *strategy* changed
            # (e.g. someone added an id). Matched, but nothing to report.
            continue
        changes.append(_change(
            "renamed", page_url, new_fp,
            previous_name=old_fp.accessible_name,
            match="structural",
        ))

    return changes, matched_old, matched_new


def diff_page_elements(
    page_url: str,
    old_fps: list[ElementFingerprint],
    new_fps: list[ElementFingerprint],
) -> list[ElementChange]:
    """All element-level changes for one page present in both snapshots."""
    changes, leftover_old, leftover_new = _pair_by_fingerprint(
        page_url, old_fps, new_fps
    )
    structural, matched_old, matched_new = _pair_by_structure(
        page_url, leftover_old, leftover_new
    )
    changes.extend(structural)

    changes.extend(
        _change("removed", page_url, fp)
        for fp in leftover_old if id(fp) not in matched_old
    )
    changes.extend(
        _change("added", page_url, fp)
        for fp in leftover_new if id(fp) not in matched_new
    )
    return changes


def _component_change(kind: str, comp: Component) -> ComponentChange:
    return ComponentChange(
        kind=kind,
        signature=comp.signature,
        component_id=comp.component_id,
        component_kind=comp.kind,
        label=comp.label,
        category=comp.category,
        role=comp.role,
        page_count=comp.page_count,
    )


def _side(analysis: Analysis) -> DiffSide:
    return DiffSide(
        source_crawl_id=analysis.source_crawl_id,
        analyzed_at=analysis.analyzed_at,
        start_url=analysis.start_url,
        page_count=len(analysis.pages),
        element_count=sum(len(p.fingerprints) for p in analysis.pages),
    )


def _page_change(
    kind: str, page: PageAnalysis, changes: list[ElementChange],
    previous_title: Optional[str] = None,
) -> PageChange:
    return PageChange(
        url=page.url,
        kind=kind,
        title=page.title,
        previous_title=previous_title,
        elements_added=sum(1 for c in changes if c.kind == "added"),
        elements_removed=sum(1 for c in changes if c.kind == "removed"),
        elements_renamed=sum(1 for c in changes if c.kind == "renamed"),
    )


def diff_analyses(old: Analysis, new: Analysis) -> Diff:
    """Compare two analyses of the same site. Pure and deterministic."""
    old_pages = {p.url: p for p in old.pages}
    new_pages = {p.url: p for p in new.pages}

    page_changes: list[PageChange] = []
    element_changes: list[ElementChange] = []

    for url in sorted(set(old_pages) - set(new_pages)):
        page = old_pages[url]
        removed = [_change("removed", url, fp) for fp in page.fingerprints]
        element_changes.extend(removed)
        page_changes.append(_page_change("removed", page, removed))

    for url in sorted(set(new_pages) - set(old_pages)):
        page = new_pages[url]
        added = [_change("added", url, fp) for fp in page.fingerprints]
        element_changes.extend(added)
        page_changes.append(_page_change("added", page, added))

    for url in sorted(set(old_pages) & set(new_pages)):
        old_page, new_page = old_pages[url], new_pages[url]
        changes = diff_page_elements(
            url, old_page.fingerprints, new_page.fingerprints
        )
        title_changed = old_page.title != new_page.title
        if not changes and not title_changed:
            continue
        element_changes.extend(changes)
        page_changes.append(_page_change(
            "changed", new_page, changes,
            previous_title=old_page.title if title_changed else None,
        ))

    old_comps = {c.signature: c for c in old.components}
    new_comps = {c.signature: c for c in new.components}
    component_changes = [
        _component_change("removed", old_comps[sig])
        for sig in sorted(set(old_comps) - set(new_comps))
    ] + [
        _component_change("added", new_comps[sig])
        for sig in sorted(set(new_comps) - set(old_comps))
    ]

    stats = {
        "pages_added": sum(1 for p in page_changes if p.kind == "added"),
        "pages_removed": sum(1 for p in page_changes if p.kind == "removed"),
        "pages_changed": sum(1 for p in page_changes if p.kind == "changed"),
        "elements_added": sum(1 for c in element_changes if c.kind == "added"),
        "elements_removed": sum(1 for c in element_changes if c.kind == "removed"),
        "elements_renamed": sum(1 for c in element_changes if c.kind == "renamed"),
        "components_added": sum(1 for c in component_changes if c.kind == "added"),
        "components_removed": sum(1 for c in component_changes if c.kind == "removed"),
    }
    stats["total_changes"] = sum(stats.values())

    return Diff(
        schema_version=SCHEMA_VERSION,
        engine_version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        old=_side(old),
        new=_side(new),
        stats=stats,
        pages=page_changes,
        elements=element_changes,
        components=component_changes,
    )


# --- CLI ---------------------------------------------------------------------


def _resolve_analysis_json(target: str) -> Path:
    p = Path(target)
    if p.is_dir():
        p = p / "analysis.json"
    if not p.exists():
        raise FileNotFoundError(
            f"No analysis.json found at {target} — run "
            f"`python -m ui_discovery.analyze {target}` first."
        )
    return p


def _load(path: Path) -> Analysis:
    return Analysis.model_validate(json.loads(path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_discovery.diff",
        description="C1 deterministic change diff between two analyses.",
    )
    parser.add_argument("old", help="Earlier analysis.json or output/<slug>/ directory.")
    parser.add_argument("new", help="Later analysis.json or output/<slug>/ directory.")
    parser.add_argument(
        "--output", default=None,
        help="Where to write the diff (default: alongside the newer analysis).",
    )
    args = parser.parse_args(argv)

    try:
        old_json = _resolve_analysis_json(args.old)
        new_json = _resolve_analysis_json(args.new)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    old, new = _load(old_json), _load(new_json)
    if old.start_url != new.start_url:
        print(f"[WARN] Comparing different start URLs: "
              f"{old.start_url} -> {new.start_url}", file=sys.stderr)

    print(f"[INFO] Diffing {old.source_crawl_id} -> {new.source_crawl_id}")
    diff = diff_analyses(old, new)

    out_dir = args.output or str(new_json.parent)
    paths = write_diff(diff, out_dir)

    s = diff.stats
    print(f"[INFO] Pages: +{s['pages_added']} -{s['pages_removed']} "
          f"~{s['pages_changed']}")
    print(f"[INFO] Elements: +{s['elements_added']} -{s['elements_removed']} "
          f"renamed {s['elements_renamed']}")
    print(f"[INFO] Components: +{s['components_added']} -{s['components_removed']}")
    for key in ("json", "markdown", "html"):
        print(f"[INFO] Wrote {paths[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
