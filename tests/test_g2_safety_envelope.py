"""G2 — the safety envelope on the record.

The engine has always refused destructive controls. Which controls, and on
whose say-so, was inferable only from the engine version — so "the probe never
clicked Delete" was folklore, and a run deliberately made *more* cautious by
config produced a manifest indistinguishable from one that was not.

Three properties these tests exist to hold:

  * **It reports what is in force, not what was asked for.** A count taken from
    the config would describe the operator's additions; the envelope has to
    describe those *plus* the defaults they were added to, because that is the
    number that governed the run.
  * **It is stable.** A manifest is diffed against the previous one far more
    often than it is read start to finish. Set iteration order leaking into it
    would make every run look changed, and a diff that always fires is one
    nobody reads.
  * **Describing the rules never becomes a way to fail.** The gates in
    `safety.py` are what actually refuse a control; this is only their
    description. A capture must not die at the last step because it could not
    write one.
"""

from __future__ import annotations

import json

import pytest

from ui_discovery.cliconfig import safety_policy
from ui_discovery.config import Scope
from ui_discovery.models import SafetyEnvelope
from ui_discovery.run import RunContext
from ui_discovery.safety import (
    ALLOW_LIST,
    BLOCK_WORDS,
    CAUTION_WORDS,
    SafetyPolicy,
    describe_envelope,
)


def _envelope(**safety) -> dict:
    """The envelope a scope config resolves to, as the pipeline builds it."""
    scope = Scope.model_validate({"safety": safety} if safety else {})
    return {**describe_envelope(safety_policy(scope)),
            "submit_forms": scope.safety.submit_forms}


# --- what the envelope says --------------------------------------------------

def test_the_envelope_names_the_primary_gate():
    """The allow-list is the gate that does the most work, so it is named in
    full rather than counted — four entries is a fact, not noise."""
    assert _envelope()["allow_list"] == sorted(ALLOW_LIST)


def test_the_word_lists_are_reported_as_what_is_in_force():
    envelope = _envelope()
    assert envelope["block_words"] == len(BLOCK_WORDS)
    assert envelope["caution_words"] == len(CAUTION_WORDS)


def test_config_additions_are_counted_on_top_of_the_defaults():
    """The number that governed the run, not the number the operator typed."""
    envelope = _envelope(block_words_extra=["Nuke", "Detonate"])
    assert envelope["block_words"] == len(BLOCK_WORDS) + 2
    assert envelope["block_words_extra"] == ["detonate", "nuke"]


def test_an_addition_already_covered_by_a_default_does_not_inflate_the_count():
    """`blocks()` is a set union. A config re-listing `delete` has added
    nothing, and a manifest claiming otherwise would overstate the envelope."""
    envelope = _envelope(block_words_extra=["delete"])
    assert envelope["block_words"] == len(BLOCK_WORDS)
    assert envelope["block_words_extra"] == ["delete"]


def test_never_touch_rules_are_named_in_full():
    """A count would be useless here: the whole value is knowing *which*
    control was ruled out, because that is what explains a gap in coverage."""
    envelope = _envelope(never_touch=["#danger-zone", "Impersonate"])
    assert envelope["never_touch"] == ["#danger-zone", "Impersonate"]


def test_forms_are_never_submitted_and_the_manifest_says_so():
    """Always False, recorded anyway. A guarantee in the artifact is worth more
    than one in a docstring."""
    assert _envelope()["submit_forms"] is False


def test_the_envelope_validates_as_a_model():
    assert SafetyEnvelope.model_validate(_envelope()).block_words == len(BLOCK_WORDS)


# --- stability ---------------------------------------------------------------

def test_the_envelope_is_stable_across_runs_of_one_config():
    """Word lists are sets. Without sorting, two runs of an unchanged config
    would differ in the manifest and every diff would report a change."""
    config = {"block_words_extra": ["zeta", "alpha", "Mu"],
              "caution_words_extra": ["yankee", "bravo"]}
    first, second = _envelope(**config), _envelope(**config)
    assert first == second
    assert first["block_words_extra"] == ["alpha", "mu", "zeta"]
    assert first["caution_words_extra"] == ["bravo", "yankee"]


# --- the acceptance criterion ------------------------------------------------

def test_two_runs_with_different_safety_configs_differ_visibly(tmp_path):
    """G2's stated acceptance, at the level a reader meets it: two `run.json`
    files, compared as a reader would."""
    def manifest_for(name: str, **safety) -> dict:
        out = tmp_path / name
        with RunContext.begin(str(out), target="https://acme.test/",
                              emit_events=False) as run:
            run.describe(safety=_envelope(**safety))
        return json.loads((out / "run.json").read_text(encoding="utf-8"))

    plain = manifest_for("plain")
    # `foreclose` deliberately is not already a default: an addition the engine
    # already covered would leave the count identical, which is correct
    # behaviour and would make this a test of nothing.
    hardened = manifest_for(
        "hardened",
        block_words_extra=["Foreclose"],
        never_touch=["#billing", "Export ledger"])

    assert plain["safety"] != hardened["safety"]
    assert hardened["safety"]["block_words"] == plain["safety"]["block_words"] + 1
    assert hardened["safety"]["never_touch"] == ["#billing", "Export ledger"]
    assert plain["safety"]["never_touch"] == []


# --- how it reaches the manifest ---------------------------------------------

def test_the_manifest_carries_the_envelope(tmp_path):
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(safety=_envelope(never_touch=["#danger"]))
    envelope = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))["safety"]
    assert envelope["allow_list"] == sorted(ALLOW_LIST)
    assert envelope["never_touch"] == ["#danger"]


def test_probe_profiles_are_folded_in_rather_than_replacing_the_envelope(tmp_path):
    """The profiles are only resolved by the crawl, so the envelope is
    completed in two steps. `describe` merges at the top level only — rebuilding
    the nested dict wrongly would silently drop everything recorded before it,
    which is the bug this test exists to catch."""
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(safety=_envelope(never_touch=["#danger"]))
        run.describe(safety={**run.safety_envelope(),
                             "probe_profiles": [{"scope": "(default)",
                                                 "tabs": "all"}]})
        envelope = run.manifest().safety

    assert envelope is not None
    assert envelope.never_touch == ["#danger"]          # survived the second call
    assert envelope.allow_list == sorted(ALLOW_LIST)    # so did this
    assert envelope.probe_profiles[0]["scope"] == "(default)"


def test_safety_envelope_returns_a_copy_not_the_live_dict(tmp_path):
    """Mutating it in place would change the manifest without going through
    `describe`, and so without its None-filtering."""
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(safety=_envelope())
        run.safety_envelope()["allow_list"] = ["everything"]
        assert run.manifest().safety.allow_list == sorted(ALLOW_LIST)


# --- describing the rules is not a way to fail -------------------------------

def test_a_run_that_never_reached_the_crawl_has_no_envelope(tmp_path):
    """`None`, not an empty envelope. A manifest claiming rules it never
    applied would be worse than one admitting it does not know."""
    with RunContext.begin(str(tmp_path), emit_events=False):
        pass
    assert json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))["safety"] is None


def test_a_failed_run_still_says_what_it_would_have_refused(tmp_path):
    """The reason the envelope is recorded before the crawl rather than after.
    A run that died is the one whose intended rules you most want to read, and
    it never reaches the point where the probe profiles are resolved."""
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(safety=_envelope(never_touch=["#danger"]))
        record = run.manifest("failed")

    assert record.outcome == "failed"
    assert record.safety is not None
    assert record.safety.never_touch == ["#danger"]
    assert record.safety.probe_profiles == []   # never got that far, and says so


def test_a_malformed_envelope_does_not_cost_the_capture(tmp_path):
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(safety={"allow_list": "not-a-list", "block_words": "many"})
        record = run.manifest()
    assert record.safety is None
    assert record.outcome == "ok"
    assert (tmp_path / "run.json").exists()


# --- end to end --------------------------------------------------------------

def test_a_real_run_records_the_envelope_it_ran_under(serve, tmp_path):
    """The unit tests above hand `describe` an envelope. This one makes the
    pipeline build its own, which is the only way to catch the envelope being
    wired to nothing — and the only way to see the probe profiles, because they
    do not exist until the crawl has resolved them."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/site")
    config = tmp_path / "scope.yaml"
    config.write_text(
        "safety:\n"
        "  block_words_extra: [Foreclose]\n"
        "  never_touch: ['#danger-zone']\n",
        encoding="utf-8")

    assert main([site.url("index.html"), "--output", str(tmp_path / "out"),
                 "--config", str(config), "--max-depth", "1", "--max-pages", "2",
                 "--headless"]) == 0

    capture = next(p for p in sorted((tmp_path / "out").iterdir()) if p.is_dir())
    envelope = json.loads(
        (capture / "run.json").read_text(encoding="utf-8"))["safety"]

    assert envelope is not None, "the pipeline built no envelope"
    assert envelope["allow_list"] == sorted(ALLOW_LIST)
    assert envelope["block_words"] == len(BLOCK_WORDS) + 1
    assert envelope["block_words_extra"] == ["foreclose"]
    assert envelope["never_touch"] == ["#danger-zone"]
    assert envelope["submit_forms"] is False
    # Resolved by the crawl, so its presence proves the two-step build worked.
    assert envelope["probe_profiles"], "probe profiles never reached the manifest"
    assert envelope["probe_profiles"][0]["scope"] == "(default)"


# --- the envelope describes the gates, so it must track them -----------------

@pytest.mark.parametrize("word", ["delete", "impersonate", "terminate"])
def test_the_counted_words_are_the_ones_that_actually_refuse(word):
    """Guards against the count and the gate drifting apart: a word counted in
    the envelope has to be a word `blocks()` will really refuse on."""
    assert word in SafetyPolicy().blocks()
    assert _envelope()["block_words"] == len(SafetyPolicy().blocks())
