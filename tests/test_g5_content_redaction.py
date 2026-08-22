"""G5 — keep people out of the captured model.

The engine has always redacted what a person *typed*. It has never redacted
what the page *displayed*, and on a logged-in portal that is the larger half:
element text, accessible names, select options, table cells and the ARIA
snapshot carry real customer names, emails and account references, and
`elements.csv` carries them again in a form built for spreadsheets.

Two failure modes, and the tests are split roughly evenly between them because
both are real:

  * **Under-redaction** — the obvious one. A seeded value survives into a
    capture and a grep finds it.
  * **Over-redaction** — the one that quietly ruins a capture. An engine that
    scrubbed every label would pass any secrets grep and produce something
    useless. Dates, order numbers, ports, viewports and version strings are
    what a UI is *made of*, and every one of them reached an early draft of the
    phone detector.

Detection is deterministic — the same rule as `safety.py`. The cost is recall:
this finds shapes, not meaning, so a name in prose is not found unless the
operator supplied it. That is stated in the module and asserted here, rather
than left for someone to discover.
"""

from __future__ import annotations

import json

import pytest

from ui_discovery.config import Scope
from ui_discovery.redact import (
    ALL_ENTITIES,
    CARD,
    DEFAULT_ENTITIES,
    DISABLED,
    EMAIL,
    PERSON,
    PHONE,
    RedactionPolicy,
    Redactor,
    build_policy,
    describe_redaction,
    iban_ok,
    luhn_ok,
)

SEEDED = (
    "alice@acme.example",
    "grace@acme.example",
    "ada@acme.example",
    "4111 1111 1111 1111",
    "GB82 WEST 1234 5698 7654 32",
    "123-45-6789",
    "+44 20 7946 0958",
    "(555) 123-4567",
)

# Planted in the fixture precisely so a capture can be checked for keeping them.
# A redactor that removed these would pass the secrets grep and be useless.
MUST_SURVIVE = (
    "1234567890123456",   # order number — 16 digits, fails Luhn
    "2026-08-22",         # ISO date
    "08.22.2026",         # dotted date
    "1-25 of 340",        # a count range
    "8080 9090",          # adjacent ports
    "12-345-678",         # a reference
    "192.168.1.1",        # an IP
    "2024.11.3",          # a version
    "1 234.56",           # money
    "Orders (12)",        # a labelled count
)


def _on(**kw) -> Redactor:
    return Redactor(RedactionPolicy(
        enabled=True, entities=frozenset(kw.pop("entities", DEFAULT_ENTITIES)), **kw))


# --- what must be found ------------------------------------------------------

@pytest.mark.parametrize("text", SEEDED)
def test_every_seeded_shape_is_detected(text):
    assert _on().text(f"Value: {text} here") != f"Value: {text} here"


@pytest.mark.parametrize("text", MUST_SURVIVE)
def test_what_a_ui_is_made_of_survives(text):
    """The over-redaction guard. Every one of these reached an early draft of
    the phone detector, and a capture that lost them would be worthless."""
    assert _on().text(f"Value: {text} here") == f"Value: {text} here"


def test_a_16_digit_order_number_is_not_a_card():
    """The Luhn check is what separates them. Without it, every long reference
    number in every portal becomes `<CARD>`."""
    assert luhn_ok("4111 1111 1111 1111")
    assert not luhn_ok("1234567890123456")
    assert _on().text("Invoice 1234567890123456") == "Invoice 1234567890123456"
    assert "<CARD>" in _on().text("Card 4111111111111111")


def test_an_iban_shaped_string_needs_its_checksum():
    assert iban_ok("GB82 WEST 1234 5698 7654 32")
    assert not iban_ok("GB00 WEST 1234 5698 7654 32")
    assert _on().text("Ref GB00WEST12345698765432") == "Ref GB00WEST12345698765432"


# --- replacement styles ------------------------------------------------------

@pytest.mark.parametrize("style, expect", [
    ("tag", "Mail <EMAIL> now"),
    ("mask", "Mail **** now"),
    ("remove", "Mail  now"),
])
def test_replace_style_round_trips(style, expect):
    assert _on(replace_style=style).text("Mail alice@acme.example now") == expect


def test_mask_does_not_leak_length():
    """A mask matching the original's length would tell a reader how long the
    account number was, which is most of what they wanted to know."""
    short = _on(replace_style="mask").text("a@b.co")
    long = _on(replace_style="mask").text("a.very.long.address@example.example")
    assert short == long == "****"


# --- entity selection --------------------------------------------------------

def test_entities_can_be_narrowed():
    only_email = _on(entities=[EMAIL])
    out = only_email.text("alice@acme.example and +44 20 7946 0958")
    assert "<EMAIL>" in out
    assert "+44 20 7946 0958" in out, "PHONE ran despite not being selected"


def test_person_names_are_operator_supplied_only():
    """A pattern cannot find a person's name. This is the seam where knowledge
    the engine cannot have gets in — and without a list it must do nothing
    rather than guess."""
    without = _on(entities=[PERSON])
    assert without.text("Ada Lovelace signed in") == "Ada Lovelace signed in"

    with_list = Redactor(RedactionPolicy(
        enabled=True, entities=frozenset([PERSON]),
        person_names=("Ada Lovelace",)))
    assert with_list.text("Ada Lovelace signed in") == "<PERSON> signed in"


def test_person_matching_is_case_insensitive_and_word_bounded():
    r = Redactor(RedactionPolicy(enabled=True, entities=frozenset([PERSON]),
                                 person_names=("Ada",)))
    assert r.text("ADA signed in") == "<PERSON> signed in"
    assert r.text("Adaptive layout") == "Adaptive layout", "matched inside a word"


# --- the policy is off unless asked for --------------------------------------

def test_redaction_is_off_by_default():
    assert Scope().privacy.redact_content is False
    assert build_policy(Scope().privacy) is DISABLED
    assert Redactor(DISABLED).text("alice@acme.example") == "alice@acme.example"


def test_an_unknown_entity_is_an_error_not_a_silent_no_op():
    """A config asking to redact `EMIAL` and being quietly ignored is the exact
    failure `test_no_dead_config` exists to prevent, one level down."""
    scope = Scope.model_validate(
        {"privacy": {"redact_content": True, "redact_entities": ["EMIAL"]}})
    with pytest.raises(ValueError, match="EMIAL"):
        build_policy(scope.privacy)


def test_an_unknown_replace_style_is_an_error():
    scope = Scope.model_validate(
        {"privacy": {"redact_content": True, "redact_style": "shred"}})
    with pytest.raises(ValueError, match="shred"):
        build_policy(scope.privacy)


def test_supplying_names_implies_the_person_entity():
    """Writing `person_names` and not also writing `PERSON` is obviously meant.
    Honouring it beats making the operator say it twice."""
    scope = Scope.model_validate({"privacy": {
        "redact_content": True, "person_names": ["Ada Lovelace"]}})
    assert PERSON in build_policy(scope.privacy).entities


def test_every_known_entity_is_reachable_from_config():
    scope = Scope.model_validate({"privacy": {
        "redact_content": True, "redact_entities": list(ALL_ENTITIES)}})
    assert build_policy(scope.privacy).entities == frozenset(ALL_ENTITIES)


# --- the posture is recorded either way --------------------------------------

def test_the_posture_says_so_when_redaction_is_off():
    """A capture that stayed silent about this would be indistinguishable from
    one where the pass ran and found nothing."""
    posture = describe_redaction(DISABLED)
    assert posture["redactions"] == []
    assert posture["content_redaction"]["enabled"] is False
    assert "off" in posture["content_redaction"]["detail"]


def test_the_posture_names_the_entities_when_on():
    posture = describe_redaction(RedactionPolicy(
        enabled=True, entities=frozenset([EMAIL, CARD]), replace_style="mask"))
    assert posture["content_redaction"]["enabled"] is True
    assert posture["content_redaction"]["entities"] == [CARD, EMAIL]
    assert posture["content_redaction"]["replace_style"] == "mask"
    assert posture["redactions"][0]["rule"] == "content.detected_entities"


def test_the_posture_admits_what_patterns_cannot_do():
    """Recall is the price of determinism. A posture that implied otherwise
    would be worse than one that says nothing."""
    detail = describe_redaction(
        RedactionPolicy(enabled=True))["content_redaction"]["detail"]
    assert "shapes, not meaning" in detail


# --- counting ----------------------------------------------------------------

def test_a_redactor_counts_what_it_found():
    r = _on()
    r.text("alice@acme.example and bob@acme.example")
    r.text("call +44 20 7946 0958")
    assert r.counts[EMAIL] == 2
    assert r.counts[PHONE] == 1
    assert r.total == 3


def test_nothing_found_leaves_the_string_identical():
    r = _on()
    assert r.text("Save") == "Save"
    assert r.total == 0


@pytest.mark.parametrize("value", [None, ""])
def test_empty_values_pass_through(value):
    assert _on().text(value) == value


# --- end to end: a real capture ---------------------------------------------

def _capture_dir(root):
    return next(p for p in sorted(root.iterdir()) if p.is_dir())


TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".html", ".csv", ".txt", ".py", ".ts", ".yaml"}


def _scan(capture, needles):
    hits = []
    scanned = 0
    for path in capture.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        scanned += 1
        body = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle in body:
                hits.append(f"{path.relative_to(capture)} contains {needle!r}")
    return scanned, hits


def test_a_real_capture_with_redaction_on_contains_no_seeded_value(serve, tmp_path):
    """G5's acceptance. Everything above tests the detector; this proves it is
    actually wired into the path a capture takes."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/pii")
    config = tmp_path / "scope.yaml"
    config.write_text(
        "privacy:\n"
        "  redact_content: true\n"
        "  person_names: ['Ada Lovelace', 'Grace Hopper']\n",
        encoding="utf-8")

    assert main([site.url("index.html"), "--output", str(tmp_path / "out"),
                 "--config", str(config), "--max-pages", "1",
                 "--headless"]) == 0

    capture = _capture_dir(tmp_path / "out")
    scanned, hits = _scan(capture, SEEDED + ("Ada Lovelace", "Grace Hopper"))
    assert scanned > 5, f"only scanned {scanned} files — the capture looks empty"
    assert not hits, "\n".join(hits)


def test_the_same_capture_keeps_what_a_ui_is_made_of(serve, tmp_path):
    """The other half, and the one that makes the first half meaningful."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/pii")
    config = tmp_path / "scope.yaml"
    config.write_text("privacy:\n  redact_content: true\n", encoding="utf-8")

    assert main([site.url("index.html"), "--output", str(tmp_path / "out"),
                 "--config", str(config), "--max-pages", "1",
                 "--headless"]) == 0

    capture = _capture_dir(tmp_path / "out")
    crawl = (capture / "crawl.json").read_text(encoding="utf-8")
    missing = [t for t in MUST_SURVIVE if t not in crawl]
    assert not missing, f"redaction destroyed a capture's own content: {missing}"


def test_without_the_config_the_capture_is_unchanged(serve, tmp_path):
    """Off by default has to mean off. A zero-config capture of this fixture
    still contains the seeded values — which is the state G5 exists to let an
    operator opt out of, and exactly why the manifest records the posture."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/pii")
    assert main([site.url("index.html"), "--output", str(tmp_path / "out"),
                 "--max-pages", "1", "--headless"]) == 0

    capture = _capture_dir(tmp_path / "out")
    crawl = (capture / "crawl.json").read_text(encoding="utf-8")
    assert "alice@acme.example" in crawl

    posture = json.loads(
        (capture / "run.json").read_text(encoding="utf-8"))["data_handling"]
    assert posture["content_redaction"]["enabled"] is False


def test_the_manifest_records_that_redaction_ran(serve, tmp_path):
    from ui_discovery.pipeline import main

    site = serve("fixtures/pii")
    config = tmp_path / "scope.yaml"
    config.write_text(
        "privacy:\n  redact_content: true\n  redact_style: mask\n",
        encoding="utf-8")

    assert main([site.url("index.html"), "--output", str(tmp_path / "out"),
                 "--config", str(config), "--max-pages", "1",
                 "--headless"]) == 0

    capture = _capture_dir(tmp_path / "out")
    posture = json.loads(
        (capture / "run.json").read_text(encoding="utf-8"))["data_handling"]
    assert posture["content_redaction"]["enabled"] is True
    assert posture["content_redaction"]["replace_style"] == "mask"
    assert any(r["rule"] == "content.detected_entities"
               for r in posture["redactions"])


# --- the probe record, which is not part of the page model -------------------

def test_the_probe_record_is_redacted_too():
    """A regression test with a story.

    `assemble_page` was treated as the single choke point every capture passes
    through. `PageNode.probe` does not: `interactions.py` builds it separately,
    so a fully-redacted page model shipped alongside a probe record carrying
    `probe.title`, every `interaction.target` and every revealed state
    unredacted. Only the end-to-end grep caught it.

    `interaction.target` is the worst of those, because the probe records every
    control it *considered*, not only the ones it clicked — so a customer name
    in a row action reaches the capture whether or not anything was pressed.
    """
    from ui_discovery.models import Element, Interaction, InteractionProbe, UIState
    from ui_discovery.redact import redact_probe

    probe = InteractionProbe(
        schema_version="0.1.0", engine_version="0", probed_at="now",
        url="u", final_url="u", title="Account alice@acme.example",
        interactions=[Interaction(target="Email alice@acme.example"),
                      Interaction(target="Save")],
        states=[UIState(
            kind="dialog", name="Contact grace@acme.example",
            trigger_label="Edit alice@acme.example", trigger_path="x",
            page_url="u", dom_path="y",
            headings=["Reach +44 20 7946 0958"],
            controls=[Element(category="button", tag="button",
                              text="Call (555) 123-4567")])],
    )
    redact_probe(probe, _on())

    assert probe.title == "Account <EMAIL>"
    assert probe.interactions[0].target == "Email <EMAIL>"
    assert probe.interactions[1].target == "Save", "an ordinary label was redacted"
    state = probe.states[0]
    assert state.name == "Contact <EMAIL>"
    assert state.trigger_label == "Edit <EMAIL>"
    assert state.headings == ["Reach <PHONE>"]
    assert state.controls[0].text == "Call <PHONE>"


def test_redacting_a_probe_is_a_no_op_when_disabled():
    from ui_discovery.models import Interaction, InteractionProbe
    from ui_discovery.redact import redact_probe

    probe = InteractionProbe(
        schema_version="0.1.0", engine_version="0", probed_at="now",
        url="u", final_url="u", title="Account alice@acme.example",
        interactions=[Interaction(target="Email alice@acme.example")])
    redact_probe(probe, Redactor(DISABLED))
    assert probe.title == "Account alice@acme.example"


def test_network_urls_are_left_to_their_own_redactor():
    """`network.redact_url` already wrote `REDACTED` into these on the way in.
    Running the content pass over them again would mangle those markers for no
    gain — the two redactions are deliberately not composed."""
    from ui_discovery.models import InteractionProbe, NetworkRequest
    from ui_discovery.redact import redact_probe

    url = "https://acme.test/api?token=REDACTED&user=alice@acme.example"
    probe = InteractionProbe(
        schema_version="0.1.0", engine_version="0", probed_at="now",
        url="u", final_url="u", title="t",
        network=[NetworkRequest(method="GET", url=url, resource_type="xhr", status=200)])
    redact_probe(probe, _on())
    assert probe.network[0].url == url
