"""G5 — keep people out of the captured model.

The engine has always redacted what a person *typed*: password fields, free-text
values, sensitive query keys. It has never redacted what the page *displayed*.
On a logged-in CRM that is the larger half by far — `Element.text`,
`accessible_name`, select options, table headers and the ARIA snapshot carry
real customer names, email addresses, phone numbers and account references, and
`elements.csv` carries them again in a form built for spreadsheets.

`G3` put the engine's data-handling promises on the record. This closes the hole
those promises were quietly leaving open.

Two rules shape everything below.

**Detection is a deterministic classifier, never a model.** Same rule as
`safety.py`, for the same reason: a redaction you cannot reproduce is one you
cannot audit, and a capture is only worth anything if it is reproducible. The
cost is recall — this finds shapes, not meaning, so it will never catch a name
in prose. That is stated rather than papered over, and `person_names` exists so
an operator can supply the part a pattern cannot find.

**Over-redaction is a real failure, not a safe default.** An engine that
scrubbed every label would pass any secrets grep and produce a useless capture.
Every detector here is deliberately narrow, each has a validator where the shape
alone is ambiguous (Luhn for cards, mod-97 for IBANs), and the test suite spends
as much effort on what must *survive* as on what must go.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# Entity kinds this module can find. `PERSON` is absent from the default set on
# purpose: it matches only names an operator supplied, so enabling it without a
# list would silently do nothing.
EMAIL = "EMAIL"
PHONE = "PHONE"
CARD = "CARD"
IBAN = "IBAN"
NATIONAL_ID = "NATIONAL_ID"
PERSON = "PERSON"

DEFAULT_ENTITIES: tuple[str, ...] = (EMAIL, PHONE, CARD, IBAN, NATIONAL_ID)
ALL_ENTITIES: tuple[str, ...] = (*DEFAULT_ENTITIES, PERSON)

# tag  — `<EMAIL>`: says what was there, which keeps the capture readable.
# mask — a fixed-width `****`. Deliberately fixed: a mask that preserved length
#        would leak how long the account number was.
# remove — nothing at all.
REPLACE_STYLES: tuple[str, ...] = ("tag", "mask", "remove")
_MASK = "****"


# --- detectors ---------------------------------------------------------------
#
# Ordering matters and is not alphabetical: a card number can satisfy a loose
# phone shape, and an IBAN contains digit runs. The longer, better-validated
# shapes are tried first so they claim their text before a looser pattern can.

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# 13-19 digits, optionally grouped by spaces or hyphens. The shape alone is not
# enough — order numbers and IDs are digit runs too — so every match is
# Luhn-checked before it counts.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Two letters, two check digits, then up to 30 alphanumerics. Validated by
# mod-97, which is what makes this safe to run against arbitrary UI text.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){10,30}\b")

# Deliberately US-SSN-shaped only. "National identifier" in general has no
# shape worth matching, and a pattern loose enough to cover every country would
# redact date ranges and part numbers instead. Narrow and honest beats broad
# and wrong; anything else belongs in `person_names` or a future entity.
_SSN_RE = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")

# A phone number, conservatively: an optional country code, then digit groups
# separated by space, hyphen, dot or parens. The leading group runs to five
# digits because UK numbers carry a five-digit prefix (`07700 900 123`) and a
# four-digit cap silently missed every one of them.
#
# The shape is permissive by design; `_phone_plausible` below is what makes it
# safe, and that split is deliberate — a regex tight enough to be correct on its
# own would be unreadable and still wrong.
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[ .-]?)?"
    r"(?:\(\d{1,5}\)[ .-]?|\d{2,5}[ .-])"
    r"\d{2,4}[ .-]?\d{2,4}(?:[ .-]?\d{2,4})?"
    r"(?![\w.])")


def luhn_ok(digits: str) -> bool:
    """The check digit that separates a card number from an order number.

    Without this, `Order 4111111111111111` and `Invoice 1234567890123456` are
    indistinguishable — and the second is far more common in the UIs this
    engine captures.
    """
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13:
        return False
    total = 0
    for index, digit in enumerate(reversed(nums)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def iban_ok(candidate: str) -> bool:
    """IBAN mod-97. Same job as Luhn: the shape is common, the checksum is not."""
    compact = candidate.replace(" ", "").upper()
    if not (15 <= len(compact) <= 34):
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        numeric = "".join(
            str(int(ch, 36)) if ch.isalpha() else ch for ch in rearranged)
    except ValueError:
        return False
    return int(numeric) % 97 == 1


# Dates reach the phone pattern from every direction: `2026-08-22`,
# `08.22.2026`, `22-08-2026`. A capture is full of them, and redacting dates
# would be an obvious, embarrassing kind of wrong.
_DATE_LIKE = re.compile(
    r"^\s*(?:\d{4}[ .-]\d{1,2}[ .-]\d{1,2}|\d{1,2}[ .-]\d{1,2}[ .-]\d{4})\s*$")


def _phone_plausible(match: str) -> bool:
    """Reject the digit groups that merely look like phone numbers.

    This is where over-redaction gets prevented, and the bar had to be raised
    twice. Viewports (`1440 x 900`), money, version strings and IP addresses
    were never a problem; dates (`2026-08-22`), reference numbers
    (`12-345-678`) and adjacent counts (`Ports 8080 9090`) all were.

    Three conditions, each earned by one of those:

      * **Not date-shaped.** No amount of digit counting rescues `08.22.2026`.
      * **9 to 15 digits.** Eight-digit groups are references and dates far
        more often than telephone numbers. This does give up 7-digit local
        numbers written without an area code — a deliberate trade, since the
        alternative redacts a capture's own identifiers.
      * **Explicit country code, a parenthesised area code, or two or more
        separators.** One separator is what `8080 9090` has; a real number
        written without a `+` is grouped more than once.
    """
    text = match.strip()
    if _DATE_LIKE.match(text):
        return False
    digits = [c for c in text if c.isdigit()]
    if not (9 <= len(digits) <= 15):
        return False
    if text.startswith("+") or "(" in text:
        return True
    return len(re.findall(r"[ .-]", text)) >= 2


@dataclass(frozen=True)
class _Detector:
    entity: str
    pattern: re.Pattern
    valid: Optional[Callable[[str], bool]] = None


_DETECTORS: tuple[_Detector, ...] = (
    _Detector(EMAIL, _EMAIL_RE),
    _Detector(IBAN, _IBAN_RE, iban_ok),
    _Detector(CARD, _CARD_RE, luhn_ok),
    _Detector(NATIONAL_ID, _SSN_RE),
    _Detector(PHONE, _PHONE_RE, _phone_plausible),
)


@dataclass(frozen=True)
class RedactionPolicy:
    """What to look for, and what to leave in its place.

    Off unless a config turns it on. Redaction is not free — it costs recall on
    a capture's own content — so it is a decision an operator makes for a target
    they know, not a default that quietly rewrites everyone's output.
    """

    enabled: bool = False
    entities: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_ENTITIES))
    replace_style: str = "tag"
    # Names an operator supplied. A pattern cannot find a person's name, so
    # this is the seam where knowledge the engine cannot have gets in.
    person_names: tuple[str, ...] = ()

    def replacement(self, entity: str) -> str:
        if self.replace_style == "remove":
            return ""
        if self.replace_style == "mask":
            return _MASK
        return f"<{entity}>"

    def active_detectors(self) -> tuple[_Detector, ...]:
        return tuple(d for d in _DETECTORS if d.entity in self.entities)

    def person_pattern(self) -> Optional[re.Pattern]:
        if PERSON not in self.entities or not self.person_names:
            return None
        names = sorted((n.strip() for n in self.person_names if n.strip()),
                       key=len, reverse=True)
        if not names:
            return None
        return re.compile(
            r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b",
            re.IGNORECASE)


DISABLED = RedactionPolicy()


class Redactor:
    """Applies a policy to text. Stateless apart from counting what it found,
    which is what lets a capture report how much was redacted rather than
    leaving a reader to wonder whether the pass ran at all."""

    def __init__(self, policy: RedactionPolicy = DISABLED) -> None:
        self.policy = policy
        self.counts: dict[str, int] = {}
        self._detectors = policy.active_detectors() if policy.enabled else ()
        self._person = policy.person_pattern() if policy.enabled else None

    @property
    def active(self) -> bool:
        return bool(self._detectors or self._person)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def text(self, value: Optional[str]) -> Optional[str]:
        """Redact one string, preserving `None` and `""` exactly.

        Returning the original object when nothing matched matters more than it
        looks: most strings in a capture are button labels, and rebuilding every
        one of them would be the bulk of the cost for none of the benefit.
        """
        if not value or not self.active:
            return value
        out = value
        for detector in self._detectors:
            def _sub(match: re.Match, entity: str = detector.entity,
                     valid: Optional[Callable[[str], bool]] = detector.valid) -> str:
                if valid and not valid(match.group(0)):
                    return match.group(0)
                self.counts[entity] = self.counts.get(entity, 0) + 1
                return self.policy.replacement(entity)
            out = detector.pattern.sub(_sub, out)
        if self._person is not None:
            def _person_sub(match: re.Match) -> str:
                self.counts[PERSON] = self.counts.get(PERSON, 0) + 1
                return self.policy.replacement(PERSON)
            out = self._person.sub(_person_sub, out)
        return out

    def each(self, values: Iterable[Optional[str]]) -> list[Optional[str]]:
        return [self.text(v) for v in values]


def build_policy(privacy) -> RedactionPolicy:
    """A policy from a scope config's `privacy` block.

    An unknown entity name is an error rather than a silent no-op: a config
    asking to redact `EMIAL` and being quietly ignored is exactly the failure
    `test_no_dead_config` exists to prevent, one level down.
    """
    if not getattr(privacy, "redact_content", False):
        return DISABLED

    requested = [e.strip().upper() for e in (privacy.redact_entities or []) if e.strip()]
    unknown = sorted(set(requested) - set(ALL_ENTITIES))
    if unknown:
        raise ValueError(
            f"privacy.redact_entities: unknown entity {', '.join(unknown)}. "
            f"Known: {', '.join(ALL_ENTITIES)}.")

    style = (privacy.redact_style or "tag").strip().lower()
    if style not in REPLACE_STYLES:
        raise ValueError(
            f"privacy.redact_style: {style!r} is not one of "
            f"{', '.join(REPLACE_STYLES)}.")

    names = tuple(privacy.person_names or ())
    entities = frozenset(requested or DEFAULT_ENTITIES)
    # Supplying names without asking for PERSON is obviously meant, so honour it
    # rather than making the operator write the entity name as well.
    if names and not requested:
        entities = entities | {PERSON}
    return RedactionPolicy(enabled=True, entities=entities,
                           replace_style=style, person_names=names)


def describe_redaction(policy: RedactionPolicy = DISABLED) -> dict:
    """G3-shaped description of what this policy removes, for the manifest.

    Reported even when redaction is off, and that is the point: a capture has to
    say which posture it ran under, or a reader cannot tell a clean capture from
    an unredacted one.
    """
    if not policy.enabled:
        return {
            "redactions": [],
            "content_redaction": {
                "enabled": False,
                "detail": (
                    "content redaction is off — captured text is recorded as "
                    "the page displayed it (privacy.redact_content)"),
            },
        }
    entities = sorted(policy.entities)
    return {
        "redactions": [
            {
                "rule": "content.detected_entities",
                "applies_to": (
                    "element text, accessible names, options, table headers, "
                    "headings, page titles and the ARIA snapshot"),
                "detail": (
                    f"{', '.join(entities)} detected by deterministic pattern "
                    f"(Luhn-checked cards, mod-97 IBANs) and replaced using "
                    f"style {policy.replace_style!r}; matching runs at capture "
                    f"time, so no unredacted copy reaches disk"),
            },
        ],
        "content_redaction": {
            "enabled": True,
            "entities": entities,
            "replace_style": policy.replace_style,
            "person_names_supplied": len(policy.person_names),
            "detail": (
                "shapes, not meaning — a name in prose is not found unless the "
                "operator supplied it in privacy.person_names"),
        },
    }


# --- applying a redactor to the model ----------------------------------------
#
# These live here rather than beside each model so that "which fields can carry
# a person" is answered in one place. It was not, at first: `assemble_page` was
# treated as the single choke point, and `PageNode.probe` — which `interactions`
# builds separately and never routes through it — sailed straight past. The
# end-to-end test caught it. One module owning the answer is what stops the next
# model from doing the same.

# Attributes whose values are text a page displayed, and so can carry a person.
# The rest of `STABLE_ATTRS` is structural — ids, roles, hrefs, classes — and
# redacting those would break selectors for no privacy gain.
_TEXTUAL_ATTRS = ("placeholder", "alt", "title", "aria-label", "value")


def redact_element(element, redactor: "Redactor"):
    """Every field of one element that can carry a person. Mutates and returns.

    `dom_path`, `role` and the geometry are untouched on purpose: they are how
    a reader finds the control again, they cannot carry a person, and a redacted
    selector is a broken one.
    """
    element.text = redactor.text(element.text)
    element.accessible_name = redactor.text(element.accessible_name)
    element.described_by = redactor.text(element.described_by)
    element.value = redactor.text(element.value)
    element.group = redactor.text(element.group)
    element.columns = [redactor.text(c) or "" for c in element.columns]
    for option in element.options:
        option.label = redactor.text(option.label) or ""
        option.value = redactor.text(option.value)
    for key in _TEXTUAL_ATTRS:
        if key in element.attributes:
            element.attributes[key] = redactor.text(element.attributes[key]) or ""
    return element


def redact_form_field(field_model, redactor: "Redactor"):
    field_model.label = redactor.text(field_model.label) or ""
    field_model.placeholder = redactor.text(field_model.placeholder)
    field_model.help_text = redactor.text(field_model.help_text)
    field_model.default = redactor.text(field_model.default)
    field_model.group = redactor.text(field_model.group)
    field_model.options = [redactor.text(o) or "" for o in field_model.options]
    return field_model


def redact_state(state, redactor: "Redactor"):
    state.name = redactor.text(state.name) or ""
    state.trigger_label = redactor.text(state.trigger_label) or ""
    state.headings = [redactor.text(h) or "" for h in state.headings]
    for control in state.controls:
        redact_element(control, redactor)
    for field_model in state.fields:
        redact_form_field(field_model, redactor)
    return state


def redact_probe(probe, redactor: "Redactor"):
    """The V3 probe record.

    `interaction.target` is an accessible name — the single most likely place a
    customer's name reaches a capture, because the probe records every control
    it considered, not only the ones it clicked. `network` is deliberately not
    touched here: URLs are redacted by `network.redact_url` on the way in, and
    doing it twice would mangle the `REDACTED` markers it already wrote.
    """
    if probe is None or not redactor.active:
        return probe
    probe.title = redactor.text(probe.title) or ""
    for interaction in probe.interactions:
        interaction.target = redactor.text(interaction.target)
    for state in probe.states:
        redact_state(state, redactor)
    return probe
