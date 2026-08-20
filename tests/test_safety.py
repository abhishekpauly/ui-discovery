"""Safety-model unit tests — pure, no browser."""

from __future__ import annotations

import pytest

from ui_discovery.network import classify, endpoint_pattern, redact_url
from ui_discovery.safety import classify_label, decide, interaction_type, should_execute


def el(**kw) -> dict:
    base = dict(category="button", tag="button", role="button",
                accessible_name="Overview", dom_path="button:nth-of-type(1)",
                visible=True, enabled=True, attributes={})
    base.update(kw)
    return base


# --- label classifier -------------------------------------------------------

def test_block_words():
    assert classify_label("Delete account") == "BLOCK"
    assert classify_label("Pay now") == "BLOCK"
    assert classify_label("Sign out") == "BLOCK"


def test_caution_words():
    assert classify_label("Save changes") == "CAUTION"
    assert classify_label("Search") == "CAUTION"


def test_safe_words():
    assert classify_label("Overview") == "SAFE"
    assert classify_label("Home") == "SAFE"
    assert classify_label(None) == "SAFE"


# --- interaction type -------------------------------------------------------

def test_type_tab():
    assert interaction_type(el(role="tab", attributes={"aria-selected": "true"})) == "tab"


def test_type_menu_from_haspopup():
    assert interaction_type(el(attributes={"aria-haspopup": "menu"})) == "menu"


def test_type_expander():
    assert interaction_type(el(attributes={"aria-expanded": "false"})) == "expander"


def test_type_disclosure_summary():
    assert interaction_type(el(tag="summary", category="other", role=None)) == "disclosure"


def test_type_navigation():
    assert interaction_type(el(category="link", role="link", tag="a")) == "navigation"


# --- decide (the two-gate decision) -----------------------------------------

def test_safe_tab_is_executed():
    d = decide(el(role="tab", accessible_name="Overview",
                  attributes={"aria-selected": "true"}))
    assert should_execute(d)


def test_destructive_menu_is_refused_despite_allowlisted_type():
    # aria-haspopup -> type 'menu' (allow-listed) BUT label BLOCK must override.
    d = decide(el(role="button", accessible_name="Delete account",
                  attributes={"aria-haspopup": "dialog"}))
    assert d.interaction_type == "menu"
    assert d.safety_label == "BLOCK"
    assert not should_execute(d)


def test_plain_button_not_executed():
    d = decide(el(accessible_name="Save changes"))
    assert not should_execute(d)  # type 'button' not allow-listed


def test_hidden_element_not_executed():
    d = decide(el(role="tab", accessible_name="Overview", visible=False,
                  attributes={"aria-selected": "true"}))
    assert not should_execute(d)
    assert "visible" in d.skipped_reason


# --- network redaction / classification -------------------------------------

def test_redact_sensitive_query():
    out = redact_url("https://api.x.com/v1/items?token=abc123&page=2")
    assert "abc123" not in out
    assert "REDACTED" in out
    assert "page=2" in out


def test_endpoint_pattern_normalizes_ids():
    assert endpoint_pattern("https://api.x.com/customers/12345/orders") \
        == "api.x.com/customers/:id/orders"


def test_classify_api_and_graphql():
    is_api, is_gql, _ = classify("POST", "https://x.com/graphql", "fetch")
    assert is_api and is_gql
    is_api2, is_gql2, _ = classify("GET", "https://x.com/page.html", "document")
    assert not is_api2 and not is_gql2


# --- word-boundary matching --------------------------------------------------
#
# BLOCK used substring matching, which on a real portal refused thirteen
# controls — six of them nonsense. These pin both halves: what must still be
# refused, and what must stop being refused.

@pytest.mark.parametrize("label", [
    "Delete", "Delete account", "DeleteAll", "Delete All Records",
    "Pay now", "PayPal", "Publish", "Approve request", "Reset password",
    "Cancel subscription", "Sign out", "Revoke access",
    # Previously caught only by accident; now explicit entries.
    "Resend Email", "Rerun pipeline", "Terminate instance", "Suspend user",
])
def test_destructive_labels_are_still_refused(label):
    assert classify_label(label) == "BLOCK", label


@pytest.mark.parametrize("label", [
    "Crunchbase",           # contains "run"
    "Omnisend",             # contains "send"
    "Payments", "Payroll",  # contain "pay"
    "Hyperwallet Payouts",
    "Rungs",                # contains "run"
    "Confirmation number",  # contains "confirm" but is a label, not an action
])
def test_words_that_merely_contain_a_block_word_are_not_refused(label):
    """Erring toward refusal is right; refusing arbitrary things is not — it
    costs coverage on every run and teaches a reader to discount the real
    refusals."""
    assert classify_label(label) != "BLOCK", label


def test_camelcase_is_split_before_matching():
    """`DeleteAll` is one word to a regex and two to a reader. Without the
    split, strict boundaries would let it through."""
    from ui_discovery.safety import normalize_label

    assert normalize_label("DeleteAll") == "delete all"
    assert normalize_label("SaveChanges") == "save changes"
    assert normalize_label("Crunchbase") == "crunchbase"


def test_caution_matching_is_unchanged():
    assert classify_label("Save changes") == "CAUTION"
    assert classify_label("Create app") == "CAUTION"
    assert classify_label("Go to customers") == "SAFE"


def test_config_added_words_also_match_on_boundaries():
    from ui_discovery.safety import SafetyPolicy

    policy = SafetyPolicy(block_words_extra=frozenset({"decommission"}))
    assert classify_label("Decommission cluster", policy) == "BLOCK"
    assert classify_label("Decommissioning guide", policy) != "BLOCK"
