"""Navigation-menu extraction.

For each page, the links inside a `navigation` landmark form a menu. Menus with
identical (label, items) are collapsed across pages into a single shared menu
(so the global primary nav shows up once, with a page count). Breadcrumb menus
are flagged by aria-label.
"""

from __future__ import annotations

import re

from ..models import NavigationMenu, PageNode

_BREADCRUMB = re.compile(r"bread\s*crumb", re.I)


def extract_navigations(pages: list[PageNode]) -> list[NavigationMenu]:
    # (label, items-tuple, is_breadcrumb) -> set of page urls
    seen: dict[tuple, set[str]] = {}
    order: list[tuple] = []

    for node in pages:
        p = node.page
        # nav elements give us labels; link items are elements in the nav landmark
        nav_labels = [
            e.accessible_name for e in p.elements if e.category == "nav"
        ]
        items = [
            e.accessible_name
            for e in p.elements
            if e.category == "link"
            and e.landmark == "navigation"
            and e.accessible_name
        ]
        if not items:
            continue
        label = nav_labels[0] if nav_labels else None
        is_crumb = bool(label and _BREADCRUMB.search(label))
        key = (label or "", tuple(items), is_crumb)
        if key not in seen:
            seen[key] = set()
            order.append(key)
        seen[key].add(node.url)

    menus: list[NavigationMenu] = []
    for key in order:
        label, items, is_crumb = key
        urls = seen[key]
        menus.append(
            NavigationMenu(
                label=label or None,
                is_breadcrumb=is_crumb,
                items=list(items),
                page_count=len(urls),
                example_pages=sorted(urls)[:5],
            )
        )
    menus.sort(key=lambda m: -m.page_count)
    return menus
