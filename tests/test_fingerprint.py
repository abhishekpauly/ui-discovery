"""Fingerprinting unit tests — pure, no browser."""

from __future__ import annotations

from ui_discovery.analysis.fingerprint import (
    fingerprint_element,
    looks_generated,
    structural_signature,
)
from ui_discovery.models import Element


def make(**kw) -> Element:
    base = dict(category="button", tag="button", role="button",
                accessible_name="Save", dom_path="main:nth-of-type(1) > button:nth-of-type(1)",
                landmark="main", attributes={})
    base.update(kw)
    return Element(**base)


def test_fingerprint_is_deterministic():
    el = make()
    a = fingerprint_element(el, "http://x.com/p")
    b = fingerprint_element(el, "http://x.com/p")
    assert a.fingerprint == b.fingerprint
    assert a.component_signature == b.component_signature


def test_generated_id_does_not_affect_fingerprint():
    # A build-generated id should be ignored, so the element stays identifiable
    # across rebuilds via its structural signature.
    plain = make(attributes={})
    with_gen_id = make(attributes={"id": "btn-a1f3c9e2"})
    assert fingerprint_element(plain, "http://x.com/p").fingerprint == \
        fingerprint_element(with_gen_id, "http://x.com/p").fingerprint
    assert fingerprint_element(with_gen_id, "http://x.com/p").strategy == "structural"


def test_stable_id_is_used_when_human_authored():
    el = make(attributes={"id": "save-button"})
    fp = fingerprint_element(el, "http://x.com/p")
    assert fp.strategy == "id"


def test_testid_takes_priority():
    el = make(attributes={"id": "save-button", "data-testid": "save"})
    assert fingerprint_element(el, "http://x.com/p").strategy == "data-testid"


def test_rename_changes_fingerprint():
    # A renamed control is a real change we want V5 to detect.
    before = make(accessible_name="Create customer")
    after = make(accessible_name="Add customer")
    assert fingerprint_element(before, "http://x.com/p").fingerprint != \
        fingerprint_element(after, "http://x.com/p").fingerprint


def test_same_shape_shares_component_signature():
    # Two sibling rows -> same shape, different position.
    a = make(dom_path="main > table:nth-of-type(1) > tr:nth-of-type(1) > a:nth-of-type(1)",
             category="link", role="link", accessible_name="View")
    b = make(dom_path="main > table:nth-of-type(1) > tr:nth-of-type(2) > a:nth-of-type(1)",
             category="link", role="link", accessible_name="View")
    fa = fingerprint_element(a, "http://x.com/p")
    fb = fingerprint_element(b, "http://x.com/p")
    assert fa.component_signature == fb.component_signature   # same component
    assert fa.fingerprint != fb.fingerprint                   # distinct instances


def test_looks_generated():
    assert looks_generated("btn-a1f3c9")
    assert looks_generated("Button__x9f2")
    assert looks_generated("item-12345")
    assert not looks_generated("save-button")
    assert not looks_generated("primary-nav")


def test_structural_signature_drops_ids():
    sig = structural_signature("div#app > main:nth-of-type(1) > button:nth-of-type(2)")
    assert "#app" not in sig
    assert "button:nth-of-type(2)" in sig
