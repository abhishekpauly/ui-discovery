"""M2 — what a crawl would do, and why, without doing it.

Scoping a real portal was guesswork until the run finished and the budget was
spent: point the engine at a target, wait forty minutes, discover that
`exclude` was one pattern too broad. `S1` made scope a decision; the decision
could still only be checked by paying for it.

Three properties are what make this artifact worth having, and each has tests
here because each is easy to lose:

  * **It never navigates.** The moment `map` opens a browser it stops being a
    thing you run while thinking.
  * **Every verdict names its rule.** "Out of scope" is useless — you cannot
    tell which line of the config to change. `exclude:/reports/**` is a
    decision.
  * **It is byte-for-byte reproducible.** Two maps of one config must be
    identical, or you cannot diff one against another to see what a config
    change did.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.config import Scope
from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.map import build_map, decide, main, write_map
from ui_discovery.models import UrlMap

ROOT = "http://a.test/"


def _scope(**kw) -> Scope:
    kw.setdefault("start_url", ROOT)
    kw.setdefault("discovery", {"sitemap": "skip"})
    return Scope(**kw)


# --- which rule decided -----------------------------------------------------


def test_an_exclude_pattern_names_itself():
    scope = _scope(scope={"exclude": ["/private/**", "/tmp/**"]})
    assert decide(ROOT + "private/x", scope, ROOT) == (False, "exclude:/private/**")


def test_an_include_pattern_names_itself():
    scope = _scope(scope={"include": ["/app/**", "/reports/**"]})
    assert decide(ROOT + "reports/q", scope, ROOT) == (True, "include:/reports/**")


def test_failing_every_include_pattern_says_so():
    scope = _scope(scope={"include": ["/app/**"]})
    assert decide(ROOT + "other", scope, ROOT) == (False, "include:no-pattern-matched")


def test_the_subdomain_policy_names_itself():
    scope = _scope(scope={"subdomains": "same-host"})
    verdict, rule = decide("http://b.test/x", scope, ROOT)
    assert (verdict, rule) == (False, "subdomain-policy:same-host")


def test_no_rules_at_all_still_gives_a_reason():
    """A bare `True` would leave a reader wondering whether the config was
    read at all."""
    assert decide(ROOT + "x", _scope(), ROOT) == (
        True, "default:everything-not-excluded")


def test_exclude_beats_include_as_it_does_in_the_crawler():
    """The map applies the gates in the crawler's own order. If it did not, it
    would predict a crawl that does not happen — which is worse than no map."""
    scope = _scope(scope={"include": ["/app/**"], "exclude": ["/app/secret/**"]})
    assert decide(ROOT + "app/secret/x", scope, ROOT)[1] == "exclude:/app/secret/**"
    assert decide(ROOT + "app/ok", scope, ROOT)[1] == "include:/app/**"


# --- the map itself ---------------------------------------------------------


def test_the_seed_is_always_on_the_map():
    url_map = build_map(ROOT, _scope(), read_sitemap=False)
    assert [e.url for e in url_map.entries] == [ROOT]
    assert url_map.entries[0].source == "seed"
    assert url_map.entries[0].depth == 0


def test_module_start_urls_are_on_the_map():
    scope = _scope(modules=[{"name": "Reports", "start_url": ROOT + "reports/"}])
    url_map = build_map(ROOT, scope, read_sitemap=False)
    sources = {e.url: e.source for e in url_map.entries}
    assert sources[ROOT + "reports"] == "module"


def test_out_of_scope_urls_are_listed_with_their_verdict():
    """Dropping them would hide the most useful thing a map can show: the rule
    that is costing you a module."""
    scope = _scope(modules=[{"name": "Private", "start_url": ROOT + "private/x"}],
                   scope={"exclude": ["/private/**"]})
    url_map = build_map(ROOT, scope, read_sitemap=False)
    excluded = [e for e in url_map.entries if not e.in_scope]
    assert [e.decided_by for e in excluded] == ["exclude:/private/**"]


def test_the_budget_is_a_verdict_too():
    """"You will run out before Reports" is exactly what a map exists to say
    before you spend forty minutes finding out."""
    modules = [{"name": f"M{i}", "start_url": f"{ROOT}m{i}"} for i in range(5)]
    scope = _scope(modules=modules, budget={"max_pages": 3})
    url_map = build_map(ROOT, scope, read_sitemap=False)

    dropped = [e for e in url_map.entries if not e.in_scope]
    assert dropped, "a budget of 3 against 6 URLs must drop some"
    assert all(e.decided_by == "budget:max_pages=3" for e in dropped)
    assert url_map.stats["in_scope"] == 3


def test_the_budget_only_applies_to_urls_that_passed_scope():
    """An excluded URL must not consume a budget slot, or the map would
    under-report what fits."""
    modules = ([{"name": "P", "start_url": ROOT + "private/x"}]
               + [{"name": f"M{i}", "start_url": f"{ROOT}m{i}"} for i in range(2)])
    scope = _scope(modules=modules, scope={"exclude": ["/private/**"]},
                   budget={"max_pages": 3})
    url_map = build_map(ROOT, scope, read_sitemap=False)
    assert url_map.stats["in_scope"] == 3  # seed + m0 + m1
    assert any(e.decided_by == "exclude:/private/**" for e in url_map.entries)


# --- reproducibility --------------------------------------------------------


def test_two_maps_of_one_config_are_byte_identical(tmp_path):
    scope = _scope(modules=[{"name": f"M{i}", "start_url": f"{ROOT}m{i}"}
                            for i in range(6)])
    first = write_map(build_map(ROOT, scope, read_sitemap=False),
                      str(tmp_path / "a"))
    second = write_map(build_map(ROOT, scope, read_sitemap=False),
                       str(tmp_path / "b"))
    assert (open(first["urls"], "rb").read()
            == open(second["urls"], "rb").read())


def test_urls_txt_carries_only_what_would_be_crawled(tmp_path):
    """It is what `H10`'s `--from` consumes, so an out-of-scope line in it
    would be an authorization bypass by copy-paste."""
    scope = _scope(modules=[{"name": "P", "start_url": ROOT + "private/x"}],
                   scope={"exclude": ["/private/**"]})
    paths = write_map(build_map(ROOT, scope, read_sitemap=False), str(tmp_path))
    lines = open(paths["urls"], encoding="utf-8").read().split()
    assert lines == [ROOT]


# --- --search ---------------------------------------------------------------


def test_search_narrows_what_is_listed():
    modules = [{"name": "A", "start_url": ROOT + "admin/one"},
               {"name": "B", "start_url": ROOT + "reports/two"}]
    url_map = build_map(ROOT, _scope(modules=modules), search="/admin/*",
                        read_sitemap=False)
    assert [e.url for e in url_map.entries] == [ROOT + "admin/one"]
    assert url_map.search == "/admin/*"


def test_search_never_changes_a_verdict():
    """A filter that also re-judged would make two maps of one config
    disagree, which is the opposite of what this artifact is for."""
    modules = [{"name": "A", "start_url": ROOT + "admin/one"},
               {"name": "B", "start_url": ROOT + "admin/two"}]
    scope = _scope(modules=modules, scope={"exclude": ["/admin/two*"]})

    full = {e.url: (e.in_scope, e.decided_by)
            for e in build_map(ROOT, scope, read_sitemap=False).entries}
    narrowed = {e.url: (e.in_scope, e.decided_by)
                for e in build_map(ROOT, scope, search="/admin/*",
                                   read_sitemap=False).entries}
    assert narrowed
    for url, verdict in narrowed.items():
        assert full[url] == verdict


# --- folding in a previous capture ------------------------------------------


@pytest.fixture
def captured(serve, tmp_path):
    """A real crawl of the link fixture, on disk, for `--from-crawl`."""
    server = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        server.url("index.html"), output_dir=str(tmp_path / "cap"),
        options=CrawlOptions(max_pages=20, max_depth=3, screenshots=False,
                             probe=False, sitemap="skip"),
    ))
    return server, crawl


def test_a_previous_crawls_urls_are_folded_in(captured, tmp_path):
    """`map` never navigates, so this is the only way `link` sources appear —
    and it is how you re-judge what the last crawl found against the config
    you are about to use, without paying for the crawl twice."""
    server, crawl = captured
    scope = _scope(start_url=server.url("index.html"))

    without = build_map(server.url("index.html"), scope, read_sitemap=False)
    with_crawl = build_map(server.url("index.html"), scope, crawl=crawl,
                           read_sitemap=False)

    assert len(without.entries) == 1
    assert len(with_crawl.entries) == len(crawl.pages)
    assert with_crawl.from_crawl == crawl.crawl_id
    assert {e.source for e in with_crawl.entries} <= {"seed", "link", "deep-nav"}


def test_a_folded_crawl_is_rejudged_against_the_new_config(captured):
    """The point of re-judging: the same URLs, a different config, and the map
    says which rule would now turn each one down."""
    server, crawl = captured
    scope = _scope(start_url=server.url("index.html"),
                   scope={"exclude": ["/orders*"]})
    url_map = build_map(server.url("index.html"), scope, crawl=crawl,
                        read_sitemap=False)

    refused = [e for e in url_map.entries if not e.in_scope]
    assert refused, "the exclude pattern must turn something down"
    assert all(e.decided_by == "exclude:/orders*" for e in refused)
    assert all("orders" in e.url for e in refused)


def test_the_map_lists_what_the_equivalent_crawl_captured(captured):
    """The acceptance criterion, stated directly: fold in a capture and the
    in-scope set is exactly what that crawl got."""
    server, crawl = captured
    scope = _scope(start_url=server.url("index.html"))
    url_map = build_map(server.url("index.html"), scope, crawl=crawl,
                        read_sitemap=False)
    assert ({e.url for e in url_map.entries if e.in_scope}
            == {n.url for n in crawl.pages})


# --- the CLI ----------------------------------------------------------------


def test_the_command_never_opens_a_browser(serve, tmp_path, capsys,
                                           monkeypatch):
    """Asserted on the absence of a launch, not on how long it took.

    The moment `map` opens a browser it stops being a thing you run while
    thinking, and a timing assertion would pass on a fast machine long after
    the property had been lost.
    """
    import playwright.async_api
    import playwright.sync_api

    def refuse(*args, **kwargs):
        raise AssertionError("map opened a browser")

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", refuse)
    monkeypatch.setattr(playwright.async_api, "async_playwright", refuse)

    server = serve("fixtures/sitemap")
    assert main([server.url("index.html"), "--output", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "Mapped" in out
    # It did read the product's own declaration - the map is not empty.
    assert "from sitemap" in out


def test_the_command_writes_both_artifacts(serve, tmp_path):
    server = serve("fixtures/sitemap")
    assert main([server.url("index.html"), "--output", str(tmp_path)]) == 0

    written = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert {"map.json", "urls.txt"} <= written

    payload = next(tmp_path.rglob("map.json")).read_text(encoding="utf-8")
    UrlMap.model_validate_json(payload)


def test_no_sitemap_makes_the_command_entirely_offline(tmp_path, capsys):
    """No network at all — the case where you are scoping from a config
    before the target is even reachable."""
    assert main(["http://unreachable.invalid/", "--no-sitemap",
                 "--output", str(tmp_path)]) == 0
    assert "Mapped 1 URL(s)" in capsys.readouterr().out


def test_a_bad_from_crawl_path_is_a_clean_error(tmp_path, capsys):
    rc = main(["http://a.test/", "--no-sitemap", "--from-crawl",
               str(tmp_path / "nope"), "--output", str(tmp_path)])
    assert rc == 1
    assert "Could not read" in capsys.readouterr().err


# --- H10's URL list is a source too ----------------------------------------
#
# `M2` predates `H10`. Without this, a dry run of a config built around `urls:`
# reported one URL and the crawl then captured seven — a preview that
# under-reports the run it is previewing is the one failure this artifact
# cannot afford. Same defect class as `G7`'s ledger ignoring `H6`'s policy: a
# feature built before a later one and never wired to it.


def test_an_explicit_url_list_is_on_the_map():
    urls = [ROOT + "a", ROOT + "b", ROOT + "c"]
    url_map = build_map(ROOT, _scope(urls=urls), read_sitemap=False)
    assert {e.url for e in url_map.entries} == {ROOT, *urls}
    assert url_map.stats["by_source"]["url-list"] == 3


def test_a_listed_url_is_judged_like_any_other():
    """A list is convenience, never an authorization — the map has to say so
    as plainly as the crawler does."""
    scope = _scope(urls=[ROOT + "app/ok", ROOT + "private/x"],
                   scope={"exclude": ["/private/**"]})
    verdicts = {e.url: (e.in_scope, e.decided_by)
                for e in build_map(ROOT, scope, read_sitemap=False).entries}
    assert verdicts[ROOT + "app/ok"][0] is True
    assert verdicts[ROOT + "private/x"] == (False, "exclude:/private/**")


def test_a_url_list_suppresses_the_sitemap_read(monkeypatch):
    """Matching the crawler: an explicit list answers the question the sitemap
    answers, so the map must not report URLs the run will never visit."""
    import ui_discovery.map as map_module

    def fail(*args, **kwargs):
        raise AssertionError("the map read a sitemap despite an explicit list")

    monkeypatch.setattr(map_module, "read_sitemaps", fail)
    url_map = build_map(ROOT, _scope(urls=[ROOT + "a"],
                                     discovery={"sitemap": "include"}))
    assert {e.url for e in url_map.entries} == {ROOT, ROOT + "a"}
