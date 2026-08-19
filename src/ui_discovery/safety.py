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

from .models import Interaction

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
}
# Ambiguous / mutating verbs -> observed, not executed (conservative in early phases).
CAUTION_WORDS = {
    "submit", "save", "apply", "update", "create", "add", "upload",
    "search", "filter", "edit", "rename", "move", "import", "export",
    "invite", "share", "download",
}

_WS = re.compile(r"\s+")


def classify_label(name: str | None) -> str:
    """SAFE / CAUTION / BLOCK from an accessible name or text."""
    if not name:
        return "SAFE"
    text = _WS.sub(" ", name.strip().lower())
    for w in BLOCK_WORDS:
        if w in text:
            return "BLOCK"
    for w in CAUTION_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", text):
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


def decide(el: dict) -> Interaction:
    """Produce an Interaction record carrying the (deterministic) decision.
    Does not touch the browser — pure classification."""
    name = el.get("accessible_name") or el.get("text")
    itype = interaction_type(el)
    label = classify_label(name)

    visible = bool(el.get("visible", True))
    enabled = bool(el.get("enabled", True))

    execute = True
    reason = None
    if not visible or not enabled:
        execute, reason = False, "not visible/enabled"
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
