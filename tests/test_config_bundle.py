"""H5 + R2 + S1 — the config bundle.

Three properties matter more than the individual toggles:

  * **Zero-config is unchanged.** A bare crawl behaves exactly as before.
  * **Precedence is flags > config > defaults**, with "not typed" reliably
    distinguishable from "typed the default".
  * **Config can only tighten safety**, never loosen it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from ui_discovery.cliconfig import auth_signals, pick, safety_policy
from ui_discovery.config import Scope, dump_scope, load_scope
from ui_discovery.crawler import crawl_site
from ui_discovery.safety import decide
from ui_discovery.util import path_matches, url_in_scope


# --- scope matching ---------------------------------------------------------

@pytest.mark.parametrize("url,pattern,expected", [
    ("http://x.test/app/home", "/app/**", True),
    ("http://x.test/app", "/app/**", False),      # ** needs the separator
    ("http://x.test/app/a/b", "/app/**", True),
    ("http://x.test/app/a/b", "/app/*", False),   # * stays within a segment
    ("http://x.test/app/a", "/app/*", True),
    ("http://x.test/admin/users", "/admin/**", True),
    ("http://x.test/logout", "/logout", True),
])
def test_path_matching(url, pattern, expected):
    assert path_matches(url, pattern) is expected


def test_exclude_beats_include():
    assert url_in_scope("http://x.test/app/ok", ["/app/**"], ["/app/secret/**"])
    assert not url_in_scope(
        "http://x.test/app/secret/x", ["/app/**"], ["/app/secret/**"])


def test_empty_include_means_everything_not_excluded():
    assert url_in_scope("http://x.test/anything", [], [])
    assert not url_in_scope("http://x.test/logout", [], ["/logout"])


# --- config loading ---------------------------------------------------------

def test_zero_config_captures_the_product():
    """A bare run should produce a capture worth reading.

    `probe` deliberately defaults to **on**: a crawl that never clicks anything
    cannot see a modal, a menu, a tab panel or an API call, which is most of
    what a portal is. It is scoped down per module with the `probe:` block, or
    off entirely with `--no-probe` — see tests/test_probe_config.py.
    """
    s = Scope()
    assert s.budget.max_pages == 25
    assert s.budget.max_depth == 3
    assert s.capabilities.screenshots is True
    assert s.capabilities.probe is True
    assert s.identity.dedupe_queries is False
    assert s.scope.include == [] and s.scope.exclude == []


def test_probe_settings_default_to_inherit():
    """Every ProbeSettings field is None until someone sets it, which is what
    makes "state only what differs from the level above" work."""
    s = Scope()
    assert s.probe.enabled is None
    assert s.probe.tabs is None
    assert s.probe.max_interactions is None
    assert s.probe.tab_labels == []


def test_no_config_path_gives_defaults():
    assert load_scope(None) == Scope()


def test_yaml_and_json_round_trip(tmp_path):
    scope = Scope(target="acme", start_url="https://acme.test/")
    for name in ("scope.yaml", "scope.json"):
        path = dump_scope(scope, str(tmp_path / name))
        assert load_scope(path).target == "acme"


def test_unknown_key_is_an_error_not_a_silent_noop(tmp_path):
    p = tmp_path / "scope.json"
    p.write_text(json.dumps({"target": "x", "budgt": {"max_pages": 5}}))
    with pytest.raises(Exception) as exc:
        load_scope(str(p))
    assert "budgt" in str(exc.value)


def test_missing_config_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_scope(str(tmp_path / "nope.yaml"))


def test_malformed_config_is_reported(tmp_path):
    p = tmp_path / "scope.json"
    p.write_text("{not json")
    with pytest.raises(ValueError):
        load_scope(str(p))


# --- precedence -------------------------------------------------------------

def test_pick_precedence():
    assert pick(5, 10, 25) == 5      # flag wins
    assert pick(None, 10, 25) == 10  # then config
    assert pick(None, None, 25) == 25  # then default


def test_flag_wins_even_when_it_equals_the_default():
    # The reason config-backed flags default to None: `--max-pages 25` with a
    # config saying 10 must yield 25, not 10.
    assert pick(25, 10, 25) == 25


# --- safety can only tighten ------------------------------------------------

def test_config_can_add_block_words():
    scope = Scope.model_validate({"safety": {"block_words_extra": ["decommission"]}})
    policy = safety_policy(scope)
    el = {"accessible_name": "Decommission cluster", "category": "button",
          "role": "tab", "attributes": {"aria-selected": "false"}}
    assert decide(el, policy).safety_label == "BLOCK"
    # ...and the built-ins still apply.
    assert decide({**el, "accessible_name": "Delete"}, policy).safety_label == "BLOCK"


def test_never_touch_blocks_execution():
    scope = Scope.model_validate({"safety": {"never_touch": ["danger-zone"]}})
    policy = safety_policy(scope)
    el = {"accessible_name": "Toggle", "category": "button", "role": "tab",
          "dom_path": "div#danger-zone > button", "attributes": {"aria-selected": "false"}}
    decision = decide(el, policy)
    assert decision.executed is False
    assert "never_touch" in decision.skipped_reason


def test_submit_forms_cannot_be_enabled():
    with pytest.raises(Exception) as exc:
        Scope.model_validate({"safety": {"submit_forms": True}})
    assert "never submits" in str(exc.value)


def test_config_extends_auth_signals_never_replaces_them():
    scope = Scope.model_validate(
        {"auth": {"login_url_patterns": ["gatekeeper"],
                  "logged_out_signals": ["please identify"]}})
    urls, phrases = auth_signals(scope)
    assert "gatekeeper" in urls and "login" in urls        # extended, not replaced
    assert "please identify" in phrases and "sign in" in phrases


# --- start-URL scoping ------------------------------------------------------

def test_start_url_from_config():
    scope = Scope(start_url="https://acme.test/app")
    assert scope.resolve_start_url(None) == "https://acme.test/app"


def test_cli_url_overrides_config_start_url():
    scope = Scope(start_url="https://acme.test/app")
    assert scope.resolve_start_url("https://other.test/") == "https://other.test/"


def test_missing_start_url_is_an_error():
    with pytest.raises(ValueError):
        Scope().resolve_start_url(None)


def test_out_of_scope_start_url_is_refused():
    scope = Scope.model_validate({"scope": {"exclude": ["/admin/**"]}})
    with pytest.raises(ValueError) as exc:
        scope.resolve_start_url("https://acme.test/admin/users")
    assert "out of scope" in str(exc.value)


# --- end to end -------------------------------------------------------------

def test_exclude_patterns_are_honoured_by_the_crawl(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3, output_dir=str(tmp_path),
        exclude=["/orders.html", "/customers.html"],
    ))
    urls = {n.url for n in crawl.pages}
    assert not any(u.endswith(("orders.html", "customers.html")) for u in urls)
    assert any(u.endswith("index.html") for u in urls)
    # The scope that produced the snapshot is recorded on it.
    assert crawl.config.exclude == ["/orders.html", "/customers.html"]


def test_include_patterns_restrict_the_crawl(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=3, output_dir=str(tmp_path),
        include=["/index.html", "/about.html"],
    ))
    urls = {n.url for n in crawl.pages}
    assert all(u.endswith(("index.html", "about.html")) for u in urls), urls


def test_screenshots_can_be_disabled(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=0, max_pages=1,
        output_dir=str(tmp_path), screenshots=False,
    ))
    assert all(n.page.screenshot_path is None for n in crawl.pages)
    assert crawl.config.capabilities["screenshots"] is False


def test_accessibility_tree_can_be_disabled(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=0, max_pages=1,
        output_dir=str(tmp_path), accessibility_tree=False,
    ))
    assert all(n.page.accessibility_tree is None for n in crawl.pages)


def test_zero_config_crawl_is_unchanged(serve, tmp_path):
    site = serve("fixtures/site")
    crawl = asyncio.run(crawl_site(
        site.url("index.html"), max_depth=1, output_dir=str(tmp_path)))
    assert crawl.pages
    assert all(n.page.screenshot_path for n in crawl.pages)
    assert crawl.config.include == [] and crawl.config.exclude == []
