"""Component detection.

Two complementary passes over the fingerprinted elements:

* shared   — the *same control* (landmark + role + accessible name) recurs on
             multiple pages. This surfaces global chrome: primary nav items,
             header/footer links, app-wide action buttons.

* repeated — the *same shape* (component_signature) occurs 2+ times within a
             single page. This surfaces list/table instances: table-row
             actions, cards, repeated menu items.

Both are deterministic and framework-agnostic.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from ..models import Component, ElementFingerprint


def _cid(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def detect_shared(
    per_page: dict[str, list[ElementFingerprint]],
) -> list[Component]:
    """Controls that recur across pages, keyed by (landmark, role, name)."""
    groups: dict[tuple, list[tuple[str, ElementFingerprint]]] = defaultdict(list)
    for url, fps in per_page.items():
        for fp in fps:
            name = (fp.accessible_name or "").strip()
            if not name:
                continue  # unnamed controls are too weak to call "shared chrome"
            key = (fp.landmark or "", fp.role or "", name.lower(), fp.category)
            groups[key].append((url, fp))

    components: list[Component] = []
    for (landmark, role, name_l, category), occ in groups.items():
        pages = {u for u, _ in occ}
        if len(pages) < 2:
            continue
        rep = occ[0][1]
        components.append(
            Component(
                component_id=_cid("shared", f"{landmark}|{role}|{name_l}|{category}"),
                kind="shared",
                signature=rep.component_signature,
                role=role or None,
                label=rep.accessible_name,
                landmark=landmark or None,
                category=category,
                page_count=len(pages),
                instance_count=len(occ),
                example_pages=sorted(pages)[:5],
            )
        )
    components.sort(key=lambda c: (-c.page_count, -c.instance_count))
    return components


def detect_repeated(
    per_page: dict[str, list[ElementFingerprint]],
) -> list[Component]:
    """Shapes that repeat within a page, keyed by component_signature."""
    # signature -> {page -> count}, plus a representative fingerprint
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rep: dict[str, ElementFingerprint] = {}
    for url, fps in per_page.items():
        for fp in fps:
            counts[fp.component_signature][url] += 1
            rep.setdefault(fp.component_signature, fp)

    components: list[Component] = []
    for sig, page_counts in counts.items():
        repeating_pages = {u: n for u, n in page_counts.items() if n >= 2}
        if not repeating_pages:
            continue
        r = rep[sig]
        total = sum(repeating_pages.values())
        components.append(
            Component(
                component_id=_cid("repeated", sig),
                kind="repeated",
                signature=sig,
                role=r.role,
                label=r.accessible_name or r.category,
                landmark=r.landmark,
                category=r.category,
                page_count=len(repeating_pages),
                instance_count=total,
                example_pages=sorted(repeating_pages)[:5],
            )
        )
    components.sort(key=lambda c: (-c.instance_count, -c.page_count))
    return components


def detect_components(
    per_page: dict[str, list[ElementFingerprint]],
) -> list[Component]:
    return detect_shared(per_page) + detect_repeated(per_page)
