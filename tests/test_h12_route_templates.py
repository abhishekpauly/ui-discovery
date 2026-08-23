"""H12 — one screen per record is one screen.

A builder, CRM or admin portal renders one screen template once per record:
`/agent-builder/<uuid>` is the same UI for every saved agent. A real capture
found eleven of its first twenty-five screens were that one screen with a
different id.

The only tool for it was a path-glob `exclude`, which is all-or-nothing — and
on that portal the choice was made once and silently cost the whole *Manage
Agent* area, including seven config tabs that were never duplicates of
anything. That is the failure this item exists to remove: not the duplication,
which was merely wasteful, but the fact that the only cure killed the patient.

Two properties carry the design, and both were observed failing before it
landed:

  * **Collapse only the path** and two records' *BasicDetails* tabs never
    merge — the duplicates survive.
  * **Collapse the whole query** and one record's seven tabs merge into one —
    the screen loses six of its sections.

Identifier-shaped *values* collapse, in path segments and in query values;
everything else stays. And the rule for "looks like an identifier" is imported
from `idpattern`, shared with `network.endpoint_pattern`, because two ideas of
what an id looks like drift and the drift is silent.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.config import Identity, Scope
from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.idpattern import ID_SEGMENT
from ui_discovery.map import build_map
from ui_discovery.util import route_template

AGENT_A = "1fe7ca07-2097-4482-9f37-12bb2da716a0"
AGENT_B = "2a2b3c4d-2097-4482-9f37-12bb2da716a0"


def agent(uuid: str, tab: str | None = None) -> str:
    base = f"https://portal.test/platform/agent-builder/{uuid}"
    return f"{base}?accountId=a68d7c22-6a65-492f-8963-a8b7b65fb015" + (
        f"&configTab={tab}" if tab else "")


# --- the template itself ----------------------------------------------------


def test_an_identifier_path_segment_collapses():
    assert route_template("https://h/agents/1fe7ca07-2097-4482-9f37-12bb2da716a0") \
        == "h/agents/:id"
    assert route_template("https://h/orders/1042") == "h/orders/:id"


def test_a_word_is_not_an_identifier():
    """Over-collapsing is the worse failure: it reports a product as having
    fewer screens than it has."""
    assert route_template("https://h/settings/general") == "h/settings/general"
    assert route_template("https://h/settings/billing") == "h/settings/billing"
    assert (route_template("https://h/settings/general")
            != route_template("https://h/settings/billing"))


def test_two_records_of_one_template_are_one_screen():
    assert route_template(agent(AGENT_A)) == route_template(agent(AGENT_B))


def test_two_tabs_of_one_record_are_two_screens():
    """The half that a path-only collapse gets wrong."""
    assert (route_template(agent(AGENT_A, "BasicDetails"))
            != route_template(agent(AGENT_A, "Instructions")))


def test_the_same_tab_of_two_records_is_one_screen():
    """The half that a whole-query collapse gets wrong."""
    assert (route_template(agent(AGENT_A, "BasicDetails"))
            == route_template(agent(AGENT_B, "BasicDetails")))


def test_identifier_query_values_collapse_and_meaningful_ones_do_not():
    template = route_template(agent(AGENT_A, "BasicDetails"))
    assert "accountId=:id" in template
    assert "configTab=BasicDetails" in template


def test_a_template_is_readable():
    """It is read by people — in the failure ledger, in the map's
    `decided_by`. `accountId=:id` is the point; `accountId=%3Aid` is a riddle.
    """
    assert "%3A" not in route_template(agent(AGENT_A, "Evals"))


def test_the_identifier_rule_is_shared_with_endpoint_patterns():
    """One rule, two callers. A second regex in the other module would drift,
    and the drift would be silent — which is exactly how `G7`'s ledger came to
    disagree with `H6`'s subdomain policy."""
    from ui_discovery import network

    assert network._ID_SEG is ID_SEGMENT


def test_an_unparseable_url_is_returned_rather_than_raising():
    assert route_template("") == ""


# --- config -----------------------------------------------------------------


def test_collapsing_is_off_by_default():
    """Nothing an existing config does may move."""
    assert Identity().collapse_instances is False
    assert Scope(start_url="http://x/").identity.collapse_instances is False


def test_a_cap_below_one_is_a_config_error():
    """`exclude` is how you capture none of a template. A cap of zero would be
    a second way to say it, and a confusing one."""
    with pytest.raises(ValueError, match="max_instances_per_route"):
        Identity(max_instances_per_route=0)


# --- the map predicts it ----------------------------------------------------


def _mapped(urls, **identity):
    scope = Scope(start_url="https://portal.test/", urls=urls,
                  discovery={"sitemap": "skip"}, identity=identity)
    return build_map("https://portal.test/", scope, read_sitemap=False)


def test_the_map_names_the_template_that_collapsed_a_url():
    url_map = _mapped([agent(AGENT_A), agent(AGENT_B)], collapse_instances=True)
    refused = [e for e in url_map.entries if not e.in_scope]
    assert len(refused) == 1
    assert refused[0].decided_by == (
        "instances:portal.test/platform/agent-builder/:id?accountId=:id")


def test_the_map_leaves_the_tabs_of_one_record_alone():
    tabs = [agent(AGENT_A, t) for t in ("BasicDetails", "Instructions", "Evals")]
    url_map = _mapped(tabs, collapse_instances=True)
    assert all(e.in_scope for e in url_map.entries), [
        (e.url, e.decided_by) for e in url_map.entries if not e.in_scope]


def test_a_collapsed_url_does_not_consume_a_budget_slot():
    """The cap runs before the budget, in the order the crawl applies them.
    Otherwise the map under-reports what fits."""
    url_map = _mapped([agent(AGENT_A), agent(AGENT_B)], collapse_instances=True)
    assert not any(e.decided_by.startswith("budget:") for e in url_map.entries)


def test_the_map_is_unchanged_when_collapsing_is_off():
    urls = [agent(AGENT_A), agent(AGENT_B)]
    assert all(e.in_scope for e in _mapped(urls).entries)


# --- through a real crawl ---------------------------------------------------


def _crawl(url, out, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out),
        options=CrawlOptions(max_pages=30, max_depth=3, screenshots=False,
                             probe=False, sitemap="skip", **kw),
    ))


@pytest.fixture
def instances(serve):
    return serve("fixtures/instances")


def test_five_records_collapse_to_one_captured_screen(instances, tmp_path):
    """One record survives — *with its tabs*. The fixture's agent screen has
    two routed tabs, so the right answer is one of each template (the screen,
    Instructions, Monitoring) and not one URL total. Collapsing the tabs away
    would be the whole-query mistake this item exists to avoid."""
    crawl = _crawl(instances.url("index.html"), tmp_path,
                   collapse_instances=True)
    agents = [n.url for n in crawl.pages if "/agents/" in n.url]
    templates = {route_template(u) for u in agents}
    assert len(templates) == 3, sorted(templates)
    assert len(agents) == 3, agents

    # And they are all the *same* record: one uuid, three views of it.
    uuids = {u.split("/agents/")[1].split("/")[0].split("?")[0] for u in agents}
    assert len(uuids) == 1, uuids


def test_the_four_it_skipped_say_why(instances, tmp_path):
    """A *skip*, not an exclusion. "You already have this screen" and "you
    asked not to have this screen" read very differently."""
    crawl = _crawl(instances.url("index.html"), tmp_path,
                   collapse_instances=True)
    skipped = [f for f in crawl.failures if f.reason == "duplicate-instance"]
    assert len(skipped) == 4, [f.model_dump() for f in crawl.failures]
    assert all("/agents/:id" in f.detail for f in skipped)
    assert all("max_instances_per_route" in f.detail for f in skipped)


def test_raising_the_cap_captures_more(instances, tmp_path):
    crawl = _crawl(instances.url("index.html"), tmp_path,
                   collapse_instances=True, max_instances_per_route=2)
    agents = [n.url for n in crawl.pages if "/agents/" in n.url]
    # Two records now, still three templates each.
    uuids = {u.split("/agents/")[1].split("/")[0].split("?")[0] for u in agents}
    assert len(uuids) == 2, uuids
    assert len(agents) == 6, agents


def test_screens_that_are_not_instances_are_untouched(instances, tmp_path):
    """`/settings/general` and `/settings/billing` are two screens, and a rule
    loose enough to merge them would be worse than no rule."""
    crawl = _crawl(instances.url("index.html"), tmp_path,
                   collapse_instances=True)
    settings = sorted(n.url.rstrip("/").rsplit("/", 1)[-1]
                      for n in crawl.pages if "/settings/" in n.url)
    assert settings == ["billing", "general"]


def test_off_by_default_reproduces_todays_crawl(instances, tmp_path):
    """The escape hatch has to be exact, or nobody can use it to isolate a
    regression."""
    crawl = _crawl(instances.url("index.html"), tmp_path)
    agents = [n.url for n in crawl.pages if "/agents/" in n.url]
    # Five records, three views each: the duplication this item removes.
    assert len(agents) == 15, agents
    assert not [f for f in crawl.failures if f.reason == "duplicate-instance"]
