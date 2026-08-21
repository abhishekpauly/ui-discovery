"""G1 — authorization is enforced, not merely recorded.

`authorized`, `authorized_by` and `environment` sat in the scope schema for
five releases being read by nothing. An operator wrote them down, believed the
run was accountable, and the engine never once consulted them.

The engine cannot verify that a person approved a capture — no software can.
What it can do is refuse to open production on nobody's authority, which is the
narrowest useful thing the fields can mean.

Two properties these tests exist to hold:

  * **The refusal happens before anything is opened.** A gate that fires after
    the first screenshot has already done the thing it was meant to prevent.
  * **It stays narrow.** A gate that fired on staging would be switched off
    within a week, and a gate that is off protects nothing.
"""

from __future__ import annotations

import json

import pytest

from ui_discovery.cliconfig import EXIT_UNAUTHORIZED, authorized_or_exit
from ui_discovery.config import Scope


def _scope(**fields) -> Scope:
    return Scope.model_validate(fields)


# --- the rule itself (pure, no process) --------------------------------------

def test_production_without_authorization_is_refused():
    refusal = _scope(environment="prod").authorization_refusal()
    assert refusal
    assert "authorized: true" in refusal
    assert "authorized_by" in refusal


def test_production_with_authorization_may_run():
    scope = _scope(environment="prod", authorized=True,
                   authorized_by="A. Paul, Head of Platform")
    assert scope.authorization_refusal() is None


def test_authorized_false_is_a_refusal_not_a_shrug():
    """Writing `authorized: false` is an operator saying "no". It would be a
    strange system that read that and started anyway."""
    scope = _scope(environment="prod", authorized=False, authorized_by="someone")
    assert scope.authorization_refusal()


@pytest.mark.parametrize("value", ["prod", "production", "PROD", " Production "])
def test_production_is_recognised_however_it_is_written(value):
    assert _scope(environment=value).authorization_refusal()


@pytest.mark.parametrize("value", ["staging", "sandbox", "dev", "preprod", ""])
def test_everywhere_else_is_untouched(value):
    """Deliberately narrow. `preprod` is not production, and guessing at it
    would refuse runs nobody asked to refuse."""
    assert _scope(environment=value).authorization_refusal() is None


def test_a_config_with_no_environment_is_untouched():
    """Most configs say nothing about environment, and zero-config runs say
    nothing at all. Neither becomes an error today."""
    assert Scope().authorization_refusal() is None
    assert _scope(target="Acme").authorization_refusal() is None


def test_a_blank_approver_does_not_count():
    """`authorized_by: "   "` is the shape of a field filled in to get past a
    check, so it does not get past the check."""
    scope = _scope(environment="prod", authorized=True, authorized_by="   ")
    assert scope.authorization_refusal()


def test_the_refusal_says_what_to_do_about_it():
    """A refusal that does not say how to proceed is just an obstacle."""
    refusal = _scope(environment="prod").authorization_refusal()
    assert "environment:" in refusal and "not production" in refusal


# --- the gate (what the CLIs do about it) ------------------------------------

def test_the_gate_exits_with_its_own_code():
    """A distinct code, so a pipeline can tell "you may not run this" from
    "the config would not parse"."""
    with pytest.raises(SystemExit) as exit_info:
        authorized_or_exit(_scope(environment="prod"))
    assert exit_info.value.code == EXIT_UNAUTHORIZED


def test_the_gate_is_silent_when_the_run_is_allowed():
    authorized_or_exit(_scope(environment="staging"))
    authorized_or_exit(_scope(environment="prod", authorized=True,
                              authorized_by="A. Paul"))


# --- end to end: nothing is opened -------------------------------------------

PROD_CONFIG = {"target": "Acme", "environment": "prod"}


def _config(tmp_path, **overrides) -> str:
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({**PROD_CONFIG, **overrides}), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("command", ["pipeline", "crawl"])
def test_no_command_navigates_when_refused(tmp_path, monkeypatch, command):
    """The assertion that matters: a refused run must not have opened a page.

    Proved by making navigation itself fail the test, rather than by checking
    for artifacts afterwards — a crawl that opened one screen and wrote nothing
    would pass that weaker check.
    """
    module = pytest.importorskip(f"ui_discovery.{command}")

    def explode(*args, **kwargs):
        raise AssertionError("a refused run reached the browser")

    monkeypatch.setattr(module, "crawl_site", explode)

    with pytest.raises(SystemExit) as exit_info:
        module.main(["https://acme.test/", "--config", _config(tmp_path),
                     "--output", str(tmp_path / "out")])
    assert exit_info.value.code == EXIT_UNAUTHORIZED
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("command", ["pipeline", "crawl"])
def test_an_authorized_production_run_gets_past_the_gate(tmp_path, command):
    """That it proceeds is shown by *how* it then fails: on the excluded start
    URL, which is checked after the gate. Cheaper than a browser, and it proves
    the same thing — the gate let it through."""
    module = pytest.importorskip(f"ui_discovery.{command}")
    config = _config(tmp_path, authorized=True, authorized_by="A. Paul",
                     scope={"exclude": ["/**"]})
    code = module.main(["https://acme.test/anything", "--config", config,
                        "--output", str(tmp_path / "out"), "--headless"])
    assert code == 1                      # the start URL, not the authorization
