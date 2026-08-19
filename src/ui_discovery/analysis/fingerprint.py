"""Element fingerprinting — the core identity primitive.

Two hashes per element:

* `fingerprint` — identifies *this element on this page*. Built from the most
  stable signal available (a human-authored `data-testid` or `id`, else a
  structural signature + role + accessible name). Designed to survive CSS
  refactors and generated-id churn so the same page can be diffed across crawls
  (V5). Deliberately DOES change when the accessible name changes — a renamed
  control is a real change we want to detect.

* `component_signature` — a coarser *shape* hash with instance text and
  positional indices removed, used to group repeated/shared components.

Nothing here assumes any frontend framework.
"""

from __future__ import annotations

import hashlib
import re

from ..models import Element, ElementFingerprint

_WS = re.compile(r"\s+")
_HEXISH = re.compile(r"[0-9a-f]{6,}", re.I)
_CSS_MODULE = re.compile(r"(__|--)[A-Za-z0-9]{4,}$")
_ID_SHORTCUT = re.compile(r"#[^ >]+")
_NTH = re.compile(r":nth-of-type\(\d+\)")


def _norm_name(name: str | None) -> str:
    if not name:
        return ""
    return _WS.sub(" ", name.strip().lower())[:80]


def looks_generated(value: str | None) -> bool:
    """Heuristic: does this id/testid look machine-generated (and therefore
    unstable across builds)?"""
    if not value:
        return True
    v = value.strip()
    if len(v) > 40:
        return True
    if _HEXISH.search(v):          # hashes, e.g. `btn-a1f3c9`
        return True
    if _CSS_MODULE.search(v):      # css-module suffixes, e.g. `Button__x9f2`
        return True
    if re.fullmatch(r"[:\w-]*\d{3,}", v):  # long numeric tails
        return True
    return False


def structural_signature(dom_path: str) -> str:
    """A CSS-refactor-resilient path: drop `#id` shortcuts, keep the
    tag[:nth-of-type] chain."""
    parts = []
    for seg in dom_path.split(" > "):
        parts.append(_ID_SHORTCUT.sub("", seg.strip()))
    return " > ".join(p for p in parts if p)


def _shape_signature(dom_path: str) -> str:
    """Structural signature with positional indices removed — the component
    'shape' shared by sibling instances."""
    return _NTH.sub("", structural_signature(dom_path))


def _sha(text: str, n: int) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def fingerprint_element(el: Element, page_url: str) -> ElementFingerprint:
    role = el.role or el.tag
    name = _norm_name(el.accessible_name or el.text)
    landmark = el.landmark or ""
    testid = el.attributes.get("data-testid")
    el_id = el.attributes.get("id")
    el_name = el.attributes.get("name")

    if testid and not looks_generated(testid):
        basis, strategy = f"testid={testid}", "data-testid"
    elif el_id and not looks_generated(el_id):
        basis, strategy = f"id={el_id}", "id"
    elif el_name and not looks_generated(el_name):
        basis, strategy = f"name={el_name}|role={role}", "name"
    else:
        basis = (
            f"struct={structural_signature(el.dom_path)}"
            f"|role={role}|name={name}|lm={landmark}"
        )
        strategy = "structural"

    fingerprint = _sha(f"{page_url}|{el.category}|{basis}", 16)

    shape = f"{el.category}|{role}|{landmark}|{_shape_signature(el.dom_path)}"
    component_signature = _sha(shape, 12)

    return ElementFingerprint(
        fingerprint=fingerprint,
        component_signature=component_signature,
        strategy=strategy,
        category=el.category,
        role=el.role,
        accessible_name=el.accessible_name,
        landmark=el.landmark,
        dom_path=el.dom_path,
    )
