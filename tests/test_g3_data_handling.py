"""G3 — the data-handling posture on the record.

The engine drops typed values, password fields, request bodies and sensitive
query values. Every one of those guarantees lived only in a docstring and a
`CONTRIBUTING.md` bullet, which meant a reader had to take the engine's word
for it — and a regression would have been invisible in exactly the artifact it
damaged.

Three properties these tests exist to hold:

  * **The manifest describes what the engine does, not what it intends.** Each
    rule is reported by the module that enforces it, and the input types are
    read out of `extract.js` rather than mirrored in Python, so a description
    cannot drift away from the behaviour it describes.
  * **Redaction is not deletion.** A capture that dropped every value would
    pass a naive secrets grep and be useless. The choice-shaped values have to
    survive, or the guarantee is worthless and the test proves nothing.
  * **The grep is the acceptance.** `G3` is only worth having if a real capture
    of a page with real secrets in it does not contain them.
"""

from __future__ import annotations

import json

import pytest

from ui_discovery.browser import _TYPED_VALUE_ROLES
from ui_discovery.browser import describe_redaction as describe_aria
from ui_discovery.config import Scope
from ui_discovery.extraction import _js_string_set
from ui_discovery.models import DataHandling
from ui_discovery.network import describe_redaction as describe_network
from ui_discovery.network import redact_url
from ui_discovery.pipeline import data_handling_posture
from ui_discovery.run import RunContext


def _posture(**privacy) -> dict:
    scope = Scope.model_validate({"privacy": privacy} if privacy else {})
    return data_handling_posture(scope)


# --- what the posture says ---------------------------------------------------

def test_every_redaction_is_named():
    """G3's acceptance, first half. A rule without an id cannot be diffed
    between two manifests, and a rule that vanished silently is the failure
    this whole item exists to prevent."""
    rules = {r["rule"] for r in _posture()["redactions"]}
    assert rules == {
        "network.query_values",
        "element.typed_values",
        "element.value_attribute",
        "aria.typed_values",
    }


def test_never_persisted_and_redacted_are_kept_apart():
    """Different strengths of promise. `never_persisted` never enters the
    model, so there is nothing to leak; `redactions` is data the engine saw and
    dropped, which is the weaker guarantee and the one worth enumerating."""
    posture = _posture()
    joined = " ".join(posture["never_persisted"]).lower()
    assert "request headers" in joined
    assert "response headers" in joined
    assert "bodies" in joined
    assert "session" in joined
    # Nothing that is merely redacted may be claimed as never-persisted.
    assert "query" not in joined


def test_config_added_network_keys_are_recorded():
    posture = _posture(redact_network_keys=["X-Tenant", "acct"])
    assert posture["network_keys_extra"] == ["acct", "x-tenant"]


def test_the_posture_validates_as_a_model():
    assert DataHandling.model_validate(_posture()).redactions[0].rule


# --- the description is read from the behaviour, never restated --------------

def test_the_recorded_value_types_come_from_the_extractor():
    """Not a mirrored list. If `extract.js` changes which types keep a value,
    the manifest changes with it — because it is the same list."""
    assert _posture()["value_recorded_for"] == _js_string_set("VALUE_SAFE_TYPES")
    assert "password" not in _posture()["value_recorded_for"]
    assert "email" not in _posture()["value_recorded_for"]
    assert "checkbox" in _posture()["value_recorded_for"]


def test_a_renamed_js_constant_fails_loudly():
    """Silently reporting an empty guarantee would be the worst outcome: the
    manifest would claim nothing is redacted while the extractor still redacts."""
    with pytest.raises(RuntimeError, match="extract.js"):
        _js_string_set("NO_SUCH_SET")


def test_the_aria_rule_names_the_roles_actually_stripped():
    detail = describe_aria()["redactions"][0]["detail"]
    for role in _TYPED_VALUE_ROLES:
        assert role in detail


def test_the_network_rule_matches_what_redact_url_really_does():
    """Guards the description against the regex drifting apart from it: every
    key the rule advertises has to be a key `redact_url` genuinely redacts."""
    detail = describe_network()["redactions"][0]["detail"]
    for key in ("token", "api_key", "secret", "password", "session", "bearer"):
        assert key in detail, f"{key} is redacted but the rule does not say so"
        assert "REDACTED" in redact_url(f"https://acme.test/x?{key}=abc123"), (
            f"the rule advertises {key} but redact_url does not redact it")


# --- how it reaches the manifest ---------------------------------------------

def test_the_manifest_carries_the_posture(tmp_path):
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(data_handling=_posture())
    posture = json.loads(
        (tmp_path / "run.json").read_text(encoding="utf-8"))["data_handling"]
    assert {r["rule"] for r in posture["redactions"]}


def test_a_run_that_described_nothing_has_no_posture(tmp_path):
    """`None`, not an empty posture. Claiming a guarantee that was never
    applied would be worse than admitting the manifest does not know."""
    with RunContext.begin(str(tmp_path), emit_events=False):
        pass
    assert json.loads(
        (tmp_path / "run.json").read_text(encoding="utf-8"))["data_handling"] is None


def test_a_malformed_posture_does_not_cost_the_capture(tmp_path):
    with RunContext.begin(str(tmp_path), emit_events=False) as run:
        run.describe(data_handling={"redactions": "not-a-list"})
        record = run.manifest()
    assert record.data_handling is None
    assert record.outcome == "ok"
    assert (tmp_path / "run.json").exists()


# --- the acceptance: grep a real capture -------------------------------------

# Planted in `fixtures/forms/index.html` precisely so this test can look for
# them. If either ever appears in a capture, the guarantee has been broken.
SECRETS = ("hunter2-should-never-be-captured", "ops@acme.example")

# Text artifacts only. Screenshots are pixels, so a grep cannot speak for them —
# and a password *rendered on screen* is exactly what `G6` exists to mask. This
# test must not be read as saying a capture contains no PII; it says the
# documented redactions hold.
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".html", ".csv", ".txt", ".py", ".ts", ".yaml"}


def test_no_typed_secret_survives_a_real_capture(serve, tmp_path):
    """G3's acceptance, second half — and the only test here that could catch
    the extractor regressing. Everything above checks the *description*."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/forms")
    assert main([site.url("index.html"), "--output", str(tmp_path),
                 "--max-pages", "2", "--headless"]) == 0

    capture = next(p for p in sorted(tmp_path.iterdir()) if p.is_dir())
    scanned, offenders = 0, []
    for path in capture.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        scanned += 1
        body = path.read_text(encoding="utf-8", errors="replace")
        for secret in SECRETS:
            if secret in body:
                offenders.append(f"{path.relative_to(capture)} contains {secret!r}")

    assert scanned > 5, f"only scanned {scanned} files — the capture looks empty"
    assert not offenders, "\n".join(offenders)


def test_the_capture_still_records_the_values_that_are_choices(serve, tmp_path):
    """Redaction is not deletion. Without this, a capture that dropped every
    value would pass the grep above and be worthless — so the guarantee has to
    be shown to be narrow, not total."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/forms")
    assert main([site.url("index.html"), "--output", str(tmp_path),
                 "--max-pages", "2", "--headless"]) == 0

    capture = next(p for p in sorted(tmp_path.iterdir()) if p.is_dir())
    crawl = json.loads((capture / "crawl.json").read_text(encoding="utf-8"))
    values = {el.get("value") for page in crawl["pages"]
              for el in page["page"]["elements"] if el.get("value")}

    assert values, "no element value survived — redaction has become deletion"
    # The number input's value is a choice and is kept; the password's is not.
    assert "3" in values
    assert not any(secret in " ".join(values) for secret in SECRETS)


def test_the_manifest_of_a_real_run_states_the_posture(serve, tmp_path):
    """The description and the capture it describes, produced by one run."""
    from ui_discovery.pipeline import main

    site = serve("fixtures/forms")
    assert main([site.url("index.html"), "--output", str(tmp_path),
                 "--max-pages", "2", "--headless"]) == 0

    capture = next(p for p in sorted(tmp_path.iterdir()) if p.is_dir())
    posture = json.loads(
        (capture / "run.json").read_text(encoding="utf-8"))["data_handling"]

    assert posture is not None, "the pipeline recorded no posture"
    assert {r["rule"] for r in posture["redactions"]} == {
        "network.query_values", "element.typed_values",
        "element.value_attribute", "aria.typed_values"}
    assert posture["value_recorded_for"] == _js_string_set("VALUE_SAFE_TYPES")
