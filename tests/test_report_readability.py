"""The crawl report has to be readable by someone who has never seen the portal.

The engine's reports used to answer "how much did you find?" — a count of
buttons, a list of URLs. Operators fed that back plainly: endpoints and URLs
were there, and nothing human-readable was. You could not tell how screens
connected, what a dropdown offered, or what a modal contained.

These tests assert the report says the things a person actually needs, in
words rather than counts. They are deliberately about *content*, not layout:
they check that the Status dropdown's options reach the page, not that they
are in a table with a particular border.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ui_discovery.crawler import crawl_site
from ui_discovery.reports import build_html, build_markdown, write_reports

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def capture(tmp_path_factory):
    """A real, fully-featured capture: two linked screens, forms, a table,
    tabs, and the probe on."""
    from tests.conftest import Server

    out = tmp_path_factory.mktemp("report")
    server = Server(FIXTURES / "forms")
    try:
        crawl = asyncio.run(crawl_site(
            f"{server.base}/index.html",
            max_pages=5, max_depth=2, output_dir=str(out), probe=True,
        ))
    finally:
        server.stop()
    return crawl, out


@pytest.fixture(scope="module")
def md(capture) -> str:
    return build_markdown(capture[0])


@pytest.fixture(scope="module")
def page_html(capture) -> str:
    return build_html(capture[0])


# --- the report still is what it was ----------------------------------------

def test_the_report_keeps_its_identity(md, page_html):
    assert "UI Crawl Report" in md
    assert "Page graph" in md
    assert "UI Crawl Report" in page_html


# --- "no human-readable UI/UX elements exist" -------------------------------

def test_every_screen_is_named_and_shown(md, capture):
    crawl, _ = capture
    for node in crawl.pages:
        assert node.page.title in md, f"{node.page.title} is missing"
    # ...with its screenshot, not just its element count.
    assert "screenshots/" in md


def test_controls_are_named_not_counted(md):
    """"3 buttons" is not an inventory. "Create order" is."""
    assert "Create order" in md
    assert "Summary" in md and "History" in md


def test_a_dropdown_reports_the_choices_it_offers(md):
    assert "Open / In progress / Closed" in md
    assert "All / Open / Closed" in md


def test_a_dropdown_reports_what_it_arrives_on(md):
    assert "In progress" in md


def test_fields_report_whether_they_are_required(md):
    assert "Customer name" in md
    assert "**yes**" in md, "no field is marked required"


def test_help_text_reaches_the_reader(md):
    assert "Use the registered trading name." in md


def test_a_radio_set_reads_as_one_question(md):
    assert "Delivery speed" in md
    assert "Standard / Express / Courier" in md


def test_a_table_reports_its_columns_and_row_actions(md):
    assert "Order, Customer, Status, Actions" in md
    assert "Per-row actions" in md


def test_destructive_potential_is_shown_per_control(md):
    """Which actions are risky is decided by the engine already; showing it
    saves a reader from clicking to find out."""
    assert "Safety" in md
    assert "CAUTION" in md


# --- "unable to find the relationship among screens" ------------------------

def test_the_report_draws_a_site_map(md):
    assert "## Site map" in md
    assert "```mermaid" in md
    assert "graph LR" in md


def test_the_site_map_labels_its_edges_with_what_you_click(md):
    assert '-- "Orders" -->' in md or '-- "Intake" -->' in md


def test_each_screen_says_how_you_get_there(md):
    assert "**How you get here:**" in md
    assert "click “Orders” on Order Intake" in md


def test_each_screen_says_where_it_leads(md):
    assert "**Where it leads:**" in md


def test_there_is_a_screen_connection_table(md):
    assert "## How the screens connect" in md
    assert "Reached by clicking" in md


# --- "unable to find the relationship among elements on a screen" -----------

def test_fields_are_grouped_under_the_form_they_belong_to(md):
    assert "**Form — New order**" in md
    assert "**Form — Filter orders**" in md


def test_a_form_reports_the_action_that_submits_it(md):
    assert "Submitted by: “Create order”" in md


def test_fields_appear_in_the_order_you_would_fill_them(md):
    """Extraction walks category by category, which put the Status dropdown
    after a checkbox in a form where it sits fourth."""
    form = md.split("**Form — New order**", 1)[1].split("**Data table", 1)[0]
    order = [line.split("|")[1].strip() for line in form.splitlines()
             if line.startswith("| ") and "---" not in line][1:]
    assert order[:4] == ["Customer name", "Contact email", "API token", "Status"], order


# --- "no screenshot capture for any new modal, form, tab, card" -------------

def test_forms_and_tables_get_their_own_picture(md):
    assert "screenshots/components/" in md


def test_a_revealed_tab_panel_is_photographed(md):
    assert "**Modals, menus and panels on this screen**" in md
    assert "screenshots/states/" in md


def test_a_revealed_state_says_what_opens_it(md):
    assert "opens when you click" in md


def test_every_referenced_screenshot_actually_exists(capture):
    """A report full of broken image links is worse than one with none."""
    crawl, out = capture
    write_reports(crawl, str(out))
    report = (out / "report.md").read_text(encoding="utf-8")
    import re

    refs = set(re.findall(r"\]\((screenshots/[^)]+)\)", report))
    assert refs, "the report references no screenshots at all"
    missing = [r for r in refs if not (out / r).exists()]
    assert not missing, f"broken image links: {missing}"


# --- honesty ----------------------------------------------------------------

def test_the_report_says_what_it_could_not_capture(md):
    assert "## Not captured" in md
    # Cards have no standard markup. Saying "0 cards" would be a claim about
    # the product; saying "we cannot see cards" is a claim about the engine,
    # and only the second one is true.
    assert "not detectable" in md
    assert "component_selectors" in md


def test_singular_counts_read_as_singular(capture):
    """A report that says "1 screens" reads like it was generated."""
    crawl, _ = capture
    one = crawl.model_copy(update={"pages": crawl.pages[:1]})
    text = build_markdown(one)
    assert "1 screens" not in text
    assert "1 screen" in text


# --- HTML carries the same facts --------------------------------------------

def test_html_shows_the_same_names_options_and_links(page_html):
    for expected in ("Create order", "Open / In progress / Closed",
                     "How the screens connect", "New order", "Recent orders",
                     "Not captured"):
        assert expected in page_html, f"{expected!r} missing from the HTML report"


def test_html_has_a_table_of_contents_for_navigating_a_big_capture(page_html):
    assert 'class="toc"' in page_html
    assert 'href="#screen-1"' in page_html


def test_html_is_theme_aware_and_does_not_scroll_sideways(page_html):
    assert "prefers-color-scheme: dark" in page_html
    assert "overflow-x: auto" in page_html


def test_html_escapes_page_content(capture):
    """Titles and labels come from the target application, which is not a
    trusted source of markup."""
    crawl, _ = capture
    hostile = crawl.model_copy(deep=True)
    hostile.pages[0].page.title = '<script>alert(1)</script>'
    out = build_html(hostile)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
