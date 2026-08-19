"""UI region inference — group a page's elements by accessibility landmark.

Regions are *inferred* from the landmark signal already captured per element,
never assumed to exist. Elements with no landmark ancestor fall into
`unlabeled`.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..models import Page, Region

# Present regions in a stable, reading-order-ish sequence.
_ORDER = [
    "banner", "navigation", "main", "form", "dialog",
    "complementary", "contentinfo", "region", "search", "unlabeled",
]


def regions_for_page(page: Page) -> list[Region]:
    buckets: dict[str, list] = defaultdict(list)
    for el in page.elements:
        buckets[el.landmark or "unlabeled"].append(el)

    regions: list[Region] = []
    for name in sorted(buckets, key=lambda n: (_ORDER.index(n) if n in _ORDER else 99, n)):
        els = buckets[name]
        cats = Counter(e.category for e in els)
        regions.append(
            Region(type=name, element_count=len(els), categories=dict(cats))
        )
    return regions
