"""X9 — capture profiles.

`Capabilities` has five switches, `ProbeSettings` several more and `Budget` a
few numbers. That is the right amount of *control* and the wrong amount of
*decision*. An operator wants to express an intent — "have a look round",
"the full documentation pass" — and in the absence of a way to say that, the
honest default is to run everything and wait.

Three things have to hold or the feature is a trap:

  * **`standard` is exactly today's behaviour**, so a config that never
    mentions this is unaffected.
  * **Explicit keys always win.** A preset that reverted a hand-set
    `screenshots: true` would be a config file arguing with its author.
  * **The capture records the resolved set, not the preset name.** Otherwise
    reading an old capture means knowing what `fast` meant in the version that
    produced it.
"""

from __future__ import annotations

import json

import pytest

from ui_discovery.config import CAPTURE_PROFILES, Outputs, Scope, load_scope


def _scope(profile: str = "standard", **kw) -> Scope:
    return Scope(start_url="http://x/", outputs={"profile": profile}, **kw)


# --- the presets themselves -------------------------------------------------


def test_standard_is_exactly_todays_defaults():
    """The whole feature is opt-in. Anyone ignoring it must be unaffected."""
    assert Outputs().profile == "standard"
    assert _scope("standard").resolved_capture() == _scope().resolved_capture()

    plain = Scope(start_url="http://x/")
    assert plain.capabilities.model_dump() == _scope("standard").capabilities.model_dump()


def test_standard_changes_nothing_because_its_preset_is_empty():
    """Stated as data rather than inferred: `standard` has no entries, so
    there is no mechanism by which it could move a setting."""
    assert CAPTURE_PROFILES["standard"] == {}


def test_fast_turns_off_what_costs_time():
    capture = _scope("fast").resolved_capture()
    assert capture["probe"] is False
    assert capture["screenshots"] is False
    assert capture["accessibility_tree"] is False
    assert capture["deep_nav"] is False


def test_deep_turns_everything_on_and_raises_the_interaction_budget():
    capture = _scope("deep").resolved_capture()
    assert capture["probe"] is True
    assert capture["screenshots"] is True
    assert capture["deep_nav"] is True
    assert capture["state_capture"] is True
    assert capture["component_screenshots"] is True
    assert capture["max_interactions"] > _scope("standard").resolved_capture()[
        "max_interactions"]


def test_a_preset_can_only_reach_settings_the_config_could_set_by_hand():
    """No preset may introduce a key that is not already a real field —
    otherwise `test_no_dead_config`'s guarantee stops covering it."""
    for name, preset in CAPTURE_PROFILES.items():
        for section, values in preset.items():
            model = Scope.model_fields[section].annotation
            for key in values:
                assert key in model.model_fields, f"{name}.{section}.{key}"


# --- explicit keys win ------------------------------------------------------


def test_an_explicit_key_survives_the_preset():
    scope = _scope("fast", capabilities={"screenshots": True})
    assert scope.capabilities.screenshots is True   # the operator said so
    assert scope.capabilities.probe is False        # the preset still applies


def test_an_explicit_key_survives_even_when_it_equals_the_preset_value():
    """`model_fields_set`, not value comparison. Someone who wrote
    `probe: false` under `fast` meant it, and would mean it just as much if
    the preset later changed."""
    scope = _scope("fast", capabilities={"probe": False})
    assert "probe" in scope.capabilities.model_fields_set


def test_an_explicit_key_survives_the_deep_preset_too():
    scope = _scope("deep", capabilities={"screenshots": False})
    assert scope.capabilities.screenshots is False
    assert scope.capabilities.deep_nav is True


def test_the_cli_override_is_injected_before_validation(tmp_path):
    """The override goes into the raw data, not onto a built model.

    Round-tripping through `model_dump()` marks every field as set, which
    would quietly turn the preset into a no-op — the failure would look like
    "the flag does nothing" and be invisible in any unit that skipped the
    file.
    """
    config = tmp_path / "scope.yaml"
    config.write_text(
        "start_url: http://x/\ncapabilities:\n  screenshots: true\n",
        encoding="utf-8")

    scope = load_scope(str(config), "fast")
    assert scope.capabilities.screenshots is True    # stated in the file
    assert scope.capabilities.probe is False         # filled by the preset
    assert scope.outputs.profile == "fast"


def test_the_cli_override_works_without_a_config_file():
    assert load_scope(None, "fast").capabilities.probe is False
    assert load_scope(None).capabilities.probe is True


# --- the record -------------------------------------------------------------


def test_the_resolved_set_is_recorded_not_the_preset_name():
    capture = _scope("fast").resolved_capture()
    # The name is kept for readability, but every toggle is spelled out
    # beside it, so an old capture is readable without the version that made
    # it.
    assert capture["profile"] == "fast"
    for key in ("screenshots", "accessibility_tree", "probe", "deep_nav",
                "network", "state_capture", "component_screenshots",
                "max_pages", "max_depth", "max_interactions"):
        assert key in capture, key


def test_two_configs_that_resolve_the_same_way_are_the_same_configuration():
    """`config_sha256` is taken over the resolved scope, so saying `fast` and
    writing out what `fast` means must be provably identical."""
    from ui_discovery.run import config_digest

    by_name = _scope("fast")
    by_hand = Scope(
        start_url="http://x/",
        outputs={"profile": "fast"},
        capabilities={"screenshots": False, "accessibility_tree": False,
                      "probe": False, "deep_nav": False},
        probe={"state_capture": False, "component_screenshots": False},
    )
    assert config_digest(by_name) == config_digest(by_hand)


def test_a_manifest_carries_the_resolved_capture(tmp_path):
    from ui_discovery.run import RunContext

    run = RunContext.begin(str(tmp_path), target="http://x/")
    run.describe(capture=_scope("deep").resolved_capture())
    payload = json.loads(run.manifest().model_dump_json())
    assert payload["capture"]["profile"] == "deep"
    assert payload["capture"]["max_interactions"] == 80


def test_a_manifest_without_a_described_capture_is_still_valid(tmp_path):
    from ui_discovery.run import RunContext

    assert RunContext.begin(str(tmp_path), target="http://x/").manifest().capture == {}


# --- a typo is an error, not a silent standard run --------------------------


def test_an_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="outputs.profile"):
        Outputs(profile="quick")


def test_every_documented_profile_is_accepted():
    for name in CAPTURE_PROFILES:
        assert Outputs(profile=name).profile == name
