"""What a control offers, what state it is in, and what it is tied to.

The engine used to report "487 buttons, 1 table, 3 inputs" — true, and useless
to anyone trying to understand the product. These tests cover the facts that
turn that into a description a person can read: the options behind a dropdown,
whether a field is required, which fieldset a radio belongs to, and which
elements on a screen are related to which.

The fixture is `fixtures/forms/` — a form with every field kind, a tab set
wired with `aria-controls`, and a data table with per-row actions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_discovery.extraction import extract_page
from ui_discovery.models import Page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


@pytest.fixture(scope="module")
def forms_page() -> Page:
    return extract_page(fixture_url("forms/index.html"))


def by_name(page: Page, name: str):
    for el in page.elements:
        if el.accessible_name == name:
            return el
    raise AssertionError(
        f"no element named {name!r}; saw "
        f"{sorted({e.accessible_name for e in page.elements if e.accessible_name})}"
    )


# --- options ----------------------------------------------------------------

def test_select_options_are_captured_with_labels(forms_page):
    status = by_name(forms_page, "Status")
    labels = [o.label for o in status.options]
    assert labels == ["Open", "In progress", "Closed"]
    assert status.option_count == 3


def test_select_records_which_option_is_selected(forms_page):
    status = by_name(forms_page, "Status")
    selected = [o.label for o in status.options if o.selected]
    assert selected == ["In progress"]
    # ...and the same fact as a plain value, so a report does not have to
    # walk the option list to say what the field arrives on.
    assert status.value == "In progress"


def test_option_values_are_kept_alongside_labels(forms_page):
    status = by_name(forms_page, "Status")
    assert [o.value for o in status.options] == ["open", "progress", "closed"]


def test_tablist_reports_its_tabs_as_options(forms_page):
    tablist = by_name(forms_page, "Order views")
    assert [o.label for o in tablist.options] == ["Summary", "History"]
    assert [o.label for o in tablist.options if o.selected] == ["Summary"]


def test_controls_without_choices_have_no_options(forms_page):
    assert by_name(forms_page, "Customer name").options == []
    assert by_name(forms_page, "Create order").options == []


# --- state ------------------------------------------------------------------

def test_required_field_says_so(forms_page):
    assert by_name(forms_page, "Customer name").states.get("required") == "true"


def test_optional_field_does_not_carry_a_noisy_false(forms_page):
    # `required: false` on every field is noise, not a finding.
    assert "required" not in by_name(forms_page, "Contact email").states


def test_checked_radio_is_read_from_the_dom_property(forms_page):
    standard = by_name(forms_page, "Standard")
    assert standard.states.get("checked") == "true"
    assert "checked" not in by_name(forms_page, "Express").states


def test_selected_tab_state_is_recorded(forms_page):
    assert by_name(forms_page, "Summary").states.get("selected") == "true"
    assert by_name(forms_page, "History").states.get("selected") == "false"


# --- values and privacy -----------------------------------------------------

def test_choice_values_are_recorded(forms_page):
    assert by_name(forms_page, "Quantity").value == "3"


def test_free_text_values_are_never_persisted(forms_page):
    """A pre-filled email is the user's data. That the field arrives populated
    is a fact about the UI; what is in it is not ours to keep."""
    email = by_name(forms_page, "Contact email")
    assert email.value is None
    assert email.states.get("has_value") == "true"


def test_password_value_appears_nowhere_in_the_snapshot(forms_page):
    secret = by_name(forms_page, "API token")
    assert secret.value is None
    assert "hunter2" not in forms_page.model_dump_json()


# --- relationships ----------------------------------------------------------

def test_fields_point_at_their_owning_form(forms_page):
    form = by_name(forms_page, "New order")
    for field in ("Customer name", "Status", "Create order"):
        assert by_name(forms_page, field).owner_form == form.dom_path


def test_radios_are_grouped_by_their_fieldset_legend(forms_page):
    groups = {by_name(forms_page, n).group
              for n in ("Standard", "Express", "Courier")}
    assert groups == {"Delivery speed"}


def test_help_text_is_attached_to_the_field_it_describes(forms_page):
    customer = by_name(forms_page, "Customer name")
    assert customer.described_by == "Use the registered trading name."


def test_tab_controls_its_panel(forms_page):
    tab = by_name(forms_page, "Summary")
    panel = by_name(forms_page, "History")  # both panels are captured regions
    assert tab.controls, "aria-controls was not resolved"
    # The tab points at a real captured element, not a dangling path.
    paths = {e.dom_path for e in forms_page.elements}
    assert tab.controls[0] in paths
    assert panel is not None


def test_parent_path_points_at_a_captured_ancestor(forms_page):
    paths = {e.dom_path for e in forms_page.elements}
    status = by_name(forms_page, "Status")
    assert status.parent_path
    assert status.parent_path in paths


def test_an_element_is_not_its_own_parent(forms_page):
    for el in forms_page.elements:
        assert el.parent_path != el.dom_path


# --- tables -----------------------------------------------------------------

def test_table_reports_its_columns_and_row_count(forms_page):
    table = by_name(forms_page, "Recent orders")
    assert table.columns == ["Order", "Customer", "Status", "Actions"]
    assert table.row_count == 2


def test_non_tables_carry_no_table_shape(forms_page):
    assert by_name(forms_page, "New order").columns == []


# --- ARIA snapshot redaction ------------------------------------------------
#
# Playwright renders a text field's current value inline. That put passwords
# and email addresses into the accessibility tree of every snapshot ever
# written — a big blob nobody reads, which is exactly why it went unnoticed.

def test_typed_values_are_stripped_from_the_aria_tree(forms_page):
    tree = forms_page.accessibility_tree
    assert "hunter2" not in tree
    assert "ops@acme.example" not in tree
    # The control itself is still there — we redact the value, not the field.
    assert 'textbox "API token"' in tree


def test_redaction_keeps_structure_and_non_typed_values():
    from ui_discovery.browser import redact_aria_snapshot

    tree = "\n".join([
        '- textbox "API token": hunter2',
        '- searchbox "Find": widgets',
        '- spinbutton "Quantity": "3"',
        '- link "Orders":',
        "  - /url: orders.html",
    ])
    out = redact_aria_snapshot(tree).splitlines()
    assert out[0] == '- textbox "API token":'
    assert out[1] == '- searchbox "Find":'
    # A spinbutton's value is a choice, not typed prose; urls are structure.
    assert out[2] == '- spinbutton "Quantity": "3"'
    assert out[4] == "  - /url: orders.html"


def test_redaction_passes_through_empty_input():
    from ui_discovery.browser import redact_aria_snapshot

    assert redact_aria_snapshot(None) is None
    assert redact_aria_snapshot("") == ""


# ============================================================================
# Phase 2 — the relationship layer over a whole crawl.
# ============================================================================

import asyncio  # noqa: E402

from ui_discovery.crawler import crawl_site  # noqa: E402
from ui_discovery.relations import (  # noqa: E402
    build_relations,
    element_links,
    forms_of,
    screen_edges,
    tables_of,
)


@pytest.fixture(scope="module")
def site_crawl(tmp_path_factory):
    """A real crawl of the multi-page fixture site, over real HTTP."""
    from tests.conftest import Server

    server = Server(FIXTURES / "site")
    try:
        return asyncio.run(crawl_site(
            f"{server.base}/index.html",
            max_pages=25, max_depth=3,
            output_dir=str(tmp_path_factory.mktemp("site")),
            probe=False,
        ))
    finally:
        server.stop()


@pytest.fixture(scope="module")
def site_relations(site_crawl):
    return build_relations(site_crawl)


# --- screen relationships ---------------------------------------------------

def test_navigation_edges_carry_the_label_you_click(site_crawl):
    """The whole point: a graph of bare URLs cannot say *how* you get there."""
    edges = screen_edges(site_crawl)
    assert edges, "no navigation edges at all"
    to_customer = [
        e for e in edges
        if e.source.endswith("customers.html") and e.target.endswith("customer-1.html")
    ]
    assert to_customer, "the Customers -> Customer 1 edge is missing"
    assert to_customer[0].label, "the edge has no label"


def test_edges_record_the_region_the_control_sits_in(site_crawl):
    edges = screen_edges(site_crawl)
    regions = {e.region for e in edges if e.region}
    assert regions & {"navigation", "main", "banner"}, (
        f"no landmark recorded on any edge; saw {regions}")


def test_every_screen_knows_how_it_is_reached_and_where_it_leads(site_relations):
    by_url = {s.url: s for s in site_relations.screens}
    customers = next(s for u, s in by_url.items() if u.endswith("customers.html"))
    assert customers.inbound, "customers.html reports no way in"
    assert customers.outbound, "customers.html reports nowhere to go"
    assert any(e.source.endswith("index.html") for e in customers.inbound)


def test_a_screen_is_not_reported_as_linking_to_itself(site_relations):
    for screen in site_relations.screens:
        assert all(e.source != e.target for e in screen.outbound)


def test_the_start_url_is_an_entry_point_not_an_orphan(site_crawl, site_relations):
    assert site_crawl.config.start_url in site_relations.entry_points
    assert site_crawl.config.start_url not in site_relations.orphans


def test_relations_stats_count_what_was_found(site_relations):
    stats = site_relations.stats
    assert stats["screens"] == len(site_relations.screens)
    assert stats["navigation_edges"] > 0
    assert stats["labelled_edges"] > 0
    assert stats["element_links"] > 0


# --- element relationships (single screen) ----------------------------------

def test_containment_links_a_form_to_its_own_fields(forms_page):
    links = element_links(forms_page)
    groups = [l for l in links if l.kind == "groups"]
    assert groups, "no form/field grouping found"
    assert any(l.source_label == "New order" and l.target_label == "Status"
               for l in groups)


def test_a_tab_is_linked_to_the_panel_it_controls(forms_page):
    controls = [l for l in element_links(forms_page) if l.kind == "controls"]
    assert any(l.source_label == "Summary" and l.target_label == "Summary"
               for l in controls), [(l.source_label, l.target_label) for l in controls]


def test_containment_links_exist_and_never_dangle(forms_page):
    paths = {e.dom_path for e in forms_page.elements}
    contains = [l for l in element_links(forms_page) if l.kind == "contains"]
    assert contains
    for link in contains:
        assert link.source in paths and link.target in paths


# --- forms ------------------------------------------------------------------

def test_a_form_is_described_by_its_fields(forms_page):
    forms = forms_of(forms_page)
    intake = next(f for f in forms if f.name == "New order")
    labels = [f.label for f in intake.fields]
    assert "Customer name" in labels
    assert "Status" in labels


def test_a_field_carries_its_options_and_default(forms_page):
    intake = next(f for f in forms_of(forms_page) if f.name == "New order")
    status = next(f for f in intake.fields if f.label == "Status")
    assert status.options == ["Open", "In progress", "Closed"]
    assert status.default == "In progress"


def test_a_required_field_is_marked_and_carries_its_help_text(forms_page):
    intake = next(f for f in forms_of(forms_page) if f.name == "New order")
    customer = next(f for f in intake.fields if f.label == "Customer name")
    assert customer.required is True
    assert customer.help_text == "Use the registered trading name."
    assert customer.placeholder == "Acme Ltd"


def test_radios_are_reported_as_one_choice_not_three_controls(forms_page):
    """Three radios named `speed` are one question. Listing them separately is
    how a form of eight questions gets documented as twenty controls."""
    intake = next(f for f in forms_of(forms_page) if f.name == "New order")
    speed = [f for f in intake.fields if f.label == "Delivery speed"]
    assert len(speed) == 1
    assert speed[0].ui_type == "radio-group"
    assert speed[0].options == ["Standard", "Express", "Courier"]
    assert speed[0].default == "Standard"
    assert not [f for f in intake.fields if f.label in ("Express", "Courier")]


def test_the_form_reports_the_action_that_submits_it(forms_page):
    intake = next(f for f in forms_of(forms_page) if f.name == "New order")
    assert "Create order" in intake.actions


# --- tables -----------------------------------------------------------------

def test_a_table_is_described_by_its_columns_and_rows(forms_page):
    table = tables_of(forms_page)[0]
    assert table.name == "Recent orders"
    assert table.columns == ["Order", "Customer", "Status", "Actions"]
    assert table.row_count == 2


def test_repeated_row_actions_are_reported_once(forms_page):
    """A "View" link on every row is one affordance, not N."""
    table = tables_of(forms_page)[0]
    assert table.row_actions == ["View"]


# --- artifacts --------------------------------------------------------------

def test_relations_json_is_written_on_every_run(site_crawl, tmp_path):
    import json

    from ui_discovery.models import Relations
    from ui_discovery.reports import write_reports

    write_reports(site_crawl, str(tmp_path))
    path = tmp_path / "relations.json"
    assert path.exists(), "relations.json was not written"
    model = Relations.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert model.screens
    assert model.stats["labelled_edges"] > 0


def test_controls_csv_lists_clickables_with_labels_and_destinations(
        site_crawl, tmp_path):
    import csv as _csv

    from ui_discovery.inventory import write_inventory

    write_inventory(site_crawl, str(tmp_path))
    path = tmp_path / "controls.csv"
    assert path.exists(), "controls.csv was not written"
    rows = list(_csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert rows
    assert all(r["label"] for r in rows if r["category"] == "link")
    assert any(r["leads_to"] for r in rows), "no control records where it goes"


# --- accessible names from content (found on a real portal) -----------------
#
# 119 of 345 unnamed elements in a real capture had visible text sitting right
# there — 44 of them column headers. The name-from-content rule was keyed off
# a hardcoded tag list plus an *explicit* `role=` attribute, so every element
# with an implicit name-from-content role lost its name.

def test_a_column_header_is_named_by_its_own_text(forms_page):
    """`<th>Order</th>` is a columnheader, and a columnheader takes its name
    from its content. A browser calls it "Order"; so must we."""
    headers = [e for e in forms_page.elements if e.category == "columnheader"]
    assert [e.accessible_name for e in headers] == [
        "Order", "Customer", "Status", "Actions"]


def test_nothing_on_a_well_marked_up_page_is_left_unnamed(forms_page):
    unnamed = [e for e in forms_page.elements
               if not (e.accessible_name or "").strip()]
    assert unnamed == [], [(e.category, e.tag, e.dom_path) for e in unnamed]


def test_name_source_is_still_reported(forms_page):
    """A reader has to be able to see *how* a name was derived."""
    header = next(e for e in forms_page.elements
                  if e.accessible_name == "Order")
    assert header.accessible_name_source == "text"


def test_an_explicit_aria_label_still_wins_over_content(forms_page):
    """Name-from-content is the fallback, not an override."""
    table = next(e for e in forms_page.elements if e.category == "table")
    assert table.accessible_name == "Recent orders"
    assert table.accessible_name_source == "aria-label"
