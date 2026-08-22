"""Deterministic interaction safety model.

Two gates, both required to *execute* an element:

  1. PRIMARY — an allow-list of structurally-safe, reversible interaction
     *types* (tab switch, expand/collapse, disclosure/menu open, details).
     Nothing outside this list is ever executed; it is only observed. This is
     an allow-list, not a deny-list, on purpose: its failure mode is "missed
     some coverage", never "clicked something destructive".

  2. SECONDARY — a word/label classifier (SAFE / CAUTION / BLOCK). It runs *in
     addition* to the type gate, never as the sole guard, so a destructively
     labelled control ("Delete", "Pay") is refused even if its type is safe.

Execute iff  (type in ALLOW_LIST)  AND  (label == "SAFE").

No LLM is involved. Both gates are data below and can be overridden by config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Interaction


@dataclass(frozen=True)
class SafetyPolicy:
    """Per-target additions to the safety envelope, from a scope config.

    Deliberately **additive only**: config can make the engine more cautious,
    never less. There is no way to remove a block word or to un-refuse a
    destructive control, because a config file is exactly the wrong place to
    be able to weaken this by accident.
    """

    block_words_extra: frozenset[str] = field(default_factory=frozenset)
    caution_words_extra: frozenset[str] = field(default_factory=frozenset)
    # Accessible names / dom_path fragments that must never be interacted
    # with, matched case-insensitively as substrings.
    never_touch: tuple[str, ...] = ()

    def blocks(self) -> set[str]:
        return BLOCK_WORDS | {w.lower() for w in self.block_words_extra}

    def cautions(self) -> set[str]:
        return CAUTION_WORDS | {w.lower() for w in self.caution_words_extra}

    def is_never_touch(self, *candidates: str | None) -> str | None:
        """Return the matching rule, or None."""
        for rule in self.never_touch:
            needle = rule.strip().lower()
            if not needle:
                continue
            for candidate in candidates:
                if candidate and needle in candidate.lower():
                    return rule
        return None


DEFAULT_POLICY = SafetyPolicy()

# Interaction types we consider structurally safe AND reversible in-page.
ALLOW_LIST = {"tab", "expander", "disclosure", "menu"}

# Destructive / state-changing verbs -> never auto-clicked.
BLOCK_WORDS = {
    "delete", "remove", "destroy", "erase", "wipe", "purge",
    "pay", "purchase", "buy", "checkout", "order now", "place order",
    "send", "approve", "reject", "execute", "run", "deploy",
    "deactivate", "disable", "archive", "revoke", "unsubscribe",
    "logout", "log out", "sign out", "reset", "cancel subscription",
    "confirm", "publish", "merge", "transfer", "withdraw",
    # Words substring matching used to catch by accident, kept deliberately
    # now that matching is on word boundaries. Re-doing an action is still
    # doing it, and these are the ones a real portal actually surfaces.
    "resend", "resubmit", "republish", "redeploy", "rerun", "retry",
    "terminate", "decommission", "suspend", "impersonate", "restore",
}
# Ambiguous / mutating verbs -> observed, not executed (conservative in early phases).
CAUTION_WORDS = {
    "submit", "save", "apply", "update", "create", "add", "upload",
    "search", "filter", "edit", "rename", "move", "import", "export",
    "invite", "share", "download",
}

_WS = re.compile(r"\s+")
# `DeleteAll` and `SaveChanges` are one word to a regex but two to a reader.
# Splitting at the case transition is what lets word-boundary matching stay
# strict without letting a camelCase label slip past it.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_label(name: str | None) -> str:
    """Lower-cased, whitespace-collapsed, camelCase split into words."""
    if not name:
        return ""
    return _WS.sub(" ", _CAMEL.sub(" ", name).strip().lower())


def _matches(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def classify_label(name: str | None, policy: SafetyPolicy = DEFAULT_POLICY) -> str:
    """SAFE / CAUTION / BLOCK from an accessible name or text.

    Both lists match on **word boundaries**. They did not always: BLOCK used
    substring matching, so on a real portal it refused "Crunchbase" (contains
    "run"), "Omnisend" and "Resend Email" ("send"), "Payments" and "Payroll"
    ("pay") — thirteen refusals, several of them nonsense.

    Erring toward refusal is right; erring toward refusing *arbitrary* things
    is not. It costs probe coverage on every run, and it trains a reader to
    discount the refusals that are real. The fix is to name the words we mean:
    anything genuinely destructive that substring matching used to catch by
    luck is now an explicit entry in BLOCK_WORDS.
    """
    text = normalize_label(name)
    if not text:
        return "SAFE"
    if any(_matches(w, text) for w in policy.blocks()):
        return "BLOCK"
    if any(_matches(w, text) for w in policy.cautions()):
        return "CAUTION"
    return "SAFE"


def interaction_type(el: dict) -> str:
    """Infer the interaction affordance from browser/ARIA signals only."""
    attrs = el.get("attributes", {}) or {}
    role = (el.get("role") or "").lower()
    tag = (el.get("tag") or "").lower()
    category = el.get("category", "")

    if role == "tab" or "aria-selected" in attrs:
        return "tab"
    if tag == "summary" or "open" in attrs and tag == "details":
        return "disclosure"
    if "aria-haspopup" in attrs:
        return "menu"
    if "aria-expanded" in attrs:
        return "expander"
    if category == "link":
        return "navigation"
    if category == "button":
        return "button"
    if category in ("input", "select", "textarea", "form"):
        return "form-control"
    return "other"


def decide(el: dict, policy: SafetyPolicy = DEFAULT_POLICY) -> Interaction:
    """Produce an Interaction record carrying the (deterministic) decision.
    Does not touch the browser — pure classification."""
    name = el.get("accessible_name") or el.get("text")
    itype = interaction_type(el)
    label = classify_label(name, policy)

    visible = bool(el.get("visible", True))
    enabled = bool(el.get("enabled", True))
    banned = policy.is_never_touch(name, el.get("dom_path"))

    execute = True
    reason = None
    if not visible or not enabled:
        execute, reason = False, "not visible/enabled"
    elif banned:
        execute, reason = False, f"matches never_touch rule {banned!r}"
    elif el.get("frame"):
        # H3: this element lives inside an iframe, so its dom_path is relative
        # to that frame. Selectors do not cross frame boundaries, so resolving
        # it against the page would either miss — or, worse, match a
        # *different* element that happens to share the path. Never click it.
        execute, reason = False, "inside an iframe (observed only)"
    elif itype not in ALLOW_LIST:
        execute, reason = False, f"type '{itype}' not on allow-list (observe only)"
    elif label != "SAFE":
        execute, reason = False, f"label {label} (observe only)"

    return Interaction(
        target=name,
        role=el.get("role"),
        category=el.get("category", ""),
        interaction_type=itype,
        dom_path=el.get("dom_path", ""),
        safety_label=label,
        executed=False,          # set True by the probe only after a real click
        skipped_reason=None if execute else reason,
    )


def should_execute(interaction: Interaction) -> bool:
    """The gate the probe checks before clicking."""
    return interaction.skipped_reason is None


def describe_envelope(policy: SafetyPolicy = DEFAULT_POLICY) -> dict:
    """G2: the rules this policy puts in force, as plain data for a manifest.

    Lives here rather than in `run.py` because the numbers have to come from
    the gates themselves. A manifest that counted the word lists by reading a
    config would report what the operator *asked for*; this reports what is
    actually in force, which is the config plus the defaults it was added to —
    and those are the two that a reader would otherwise have to reconcile by
    hand.

    Sorted throughout: a manifest is diffed against the previous one far more
    often than it is read start to finish, and set iteration order would make
    every run look changed.
    """
    return {
        "allow_list": sorted(ALLOW_LIST),
        "block_words": len(policy.blocks()),
        "caution_words": len(policy.cautions()),
        "block_words_extra": sorted(w.lower() for w in policy.block_words_extra),
        "caution_words_extra": sorted(w.lower() for w in policy.caution_words_extra),
        "never_touch": list(policy.never_touch),
    }
