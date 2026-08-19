"""R3 — the adapter seam.

Two properties matter: an adapter selected in config genuinely changes
behavior, and with no adapters nothing changes at all. Plus the failure
modes — an unknown adapter is loud, a broken one is not fatal.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery import adapters as hooks
from ui_discovery.adapters import Adapter, available, build, register
from ui_discovery.config import Scope
from ui_discovery.crawler import crawl_site
from ui_discovery.models import Element, Page


def _page(**kw) -> Page:
    base = dict(
        schema_version="0.1.0", engine_version="0", extracted_at="now",
        requested_url="http://x.test/", final_url="http://x.test/", title="T",
    )
    base.update(kw)
    return Page(**base)


# --- registry ---------------------------------------------------------------

def test_builtins_are_registered():
    for name in ("extra_wait", "extra_headers", "skip_paths", "logged_in_marker"):
        assert name in available()


def test_build_from_config_specs():
    built = build([{"name": "skip_paths", "options": {"patterns": ["/x"]}}])
    assert len(built) == 1 and built[0].name == "skip_paths"


def test_unknown_adapter_is_an_error():
    # A config asking for behavior the engine cannot provide has not been
    # honored — failing loudly beats a capture that ignored its own scope.
    with pytest.raises(ValueError) as exc:
        build([{"name": "does_not_exist"}])
    assert "Unknown adapter" in str(exc.value)


def test_adapter_needs_a_name_to_register():
    class Nameless(Adapter):
        pass

    with pytest.raises(ValueError):
        register(Nameless)


def test_config_accepts_an_adapters_block():
    scope = Scope.model_validate(
        {"adapters": [{"name": "extra_wait", "options": {"ms": 500}}]})
    assert scope.adapters[0].name == "extra_wait"
    assert scope.adapters[0].options == {"ms": 500}


# --- combining opinions -----------------------------------------------------

def test_should_visit_one_veto_is_enough():
    skip = build([{"name": "skip_paths", "options": {"patterns": [r"/admin"]}}])
    assert hooks.should_visit(skip, "http://x.test/app") is True
    assert hooks.should_visit(skip, "http://x.test/admin/users") is False


def test_no_adapters_means_no_opinion():
    assert hooks.should_visit([], "http://x.test/anything") is True
    assert hooks.is_logged_in([], _page()) is None


def test_is_logged_in_false_wins():
    class SaysYes(Adapter):
        name = "_t_yes"

        def is_logged_in(self, page):
            return True

    class SaysNo(Adapter):
        name = "_t_no"

        def is_logged_in(self, page):
            return False

    # A false negative here means silently capturing login screens, so the
    # pessimistic verdict wins.
    assert hooks.is_logged_in([SaysYes(), SaysNo()], _page()) is False
    assert hooks.is_logged_in([SaysYes()], _page()) is True


def test_logged_in_marker_reads_controls():
    adapter = build([{"name": "logged_in_marker",
                      "options": {"requires_control": "Account menu",
                                  "forbids_control": "Continue with Google"}}])[0]
    signed_in = _page(elements=[Element(category="button", tag="button",
                                        accessible_name="Account menu")])
    signed_out = _page(elements=[Element(category="button", tag="button",
                                         accessible_name="Continue with Google")])
    assert adapter.is_logged_in(signed_in) is True
    assert adapter.is_logged_in(signed_out) is False


def test_a_broken_adapter_does_not_take_down_the_run():
    class Broken(Adapter):
        name = "_t_broken"

        def should_visit(self, url):
            raise RuntimeError("boom")

        def is_logged_in(self, page):
            raise RuntimeError("boom")

    # Treated as "no opinion", not as a crash.
    assert hooks.should_visit([Broken()], "http://x.test/") is True
    assert hooks.is_logged_in([Broken()], _page()) is None


# --- end to end -------------------------------------------------------------

def test_adapter_narrows_the_crawl(serve, tmp_path):
    site = serve("fixtures/site")
    skip = build([{"name": "skip_paths",
                   "options": {"patterns": [r"orders\.html", r"order-\d+"]}}])
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3,
        output_dir=str(tmp_path), adapters=skip,
    ))
    urls = {n.url for n in crawl.pages}
    assert not any("order" in u for u in urls), urls
    assert any(u.endswith("index.html") for u in urls)


def test_no_adapters_leaves_the_crawl_unchanged(serve, tmp_path):
    site = serve("fixtures/site")
    plain = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=2, output_dir=str(tmp_path)))
    with_empty = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=2,
        output_dir=str(tmp_path), adapters=[]))
    assert {n.url for n in plain.pages} == {n.url for n in with_empty.pages}


def test_post_navigate_hook_runs(serve, tmp_path):
    calls: list[str] = []

    class Recorder(Adapter):
        name = "_t_recorder"

        async def post_navigate(self, page):
            calls.append("post_navigate")

        def on_page(self, page):
            calls.append("on_page")

    asyncio.run(crawl_site(
        serve("fixtures/site").url("index.html"), max_depth=0, max_pages=1,
        output_dir=str(tmp_path), adapters=[Recorder()],
    ))
    assert "post_navigate" in calls
    assert "on_page" in calls
    # The wait must happen before the page is read, not after.
    assert calls.index("post_navigate") < calls.index("on_page")


def test_adapter_can_override_the_logged_in_verdict(serve, tmp_path):
    class AlwaysLoggedOut(Adapter):
        name = "_t_always_out"

        def is_logged_in(self, page):
            return False

    crawl = asyncio.run(crawl_site(
        serve("fixtures/site").url("index.html"), max_depth=0, max_pages=1,
        output_dir=str(tmp_path), adapters=[AlwaysLoggedOut()],
    ))
    node = crawl.pages[0]
    assert node.page.auth.looks_logged_out is True
    assert node.page.auth.signal == "adapter"
