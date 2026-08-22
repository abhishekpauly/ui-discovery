"""Guard: every scope-config field is actually consumed by something.

`modules:` sat in the schema for a whole feature cycle being read by nothing.
A config key that silently does nothing is worse than no key at all — the
operator writes it down, believes the run was scoped, and it wasn't.

This walks the `Scope` schema and fails if a field name appears nowhere in
the source outside its own definition. It is a crude check on purpose: a
name that is merely *mentioned* passes, so it cannot prove a field is
honoured — only catch one that is entirely orphaned, which is the failure
that actually happened.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ui_discovery.config import Scope

SRC = Path(__file__).resolve().parents[1] / "src" / "ui_discovery"
# Where fields are *declared*; a mention here doesn't count as being used.
DECLARATIONS = {"config.py"}
# Recorded on the snapshot for provenance rather than driving behavior.
# `authorized`, `authorized_by` and `environment` left this set in G1: they now
# gate a production run rather than annotate it. This check is too crude to
# prove that — a name that is merely mentioned passes — so the real proof lives
# in `tests/test_g1_authorization.py`.
DOCUMENTED_AS_METADATA = {
    "target", "known_endpoints", "name", "formats",
}
# Fields whose entire job is to be *refused* — the validator in config.py is
# the consumption. `submit_forms: true` exists so that writing it produces a
# clear error rather than being silently ignored; the engine fills forms and
# never submits them, and no code path can change that.
ENFORCED_AT_VALIDATION = {"submit_forms"}


def _scope_fields() -> set[str]:
    """Every field name in the Scope schema, including nested models."""
    names: set[str] = set()

    def walk(model) -> None:
        for field_name, field in model.model_fields.items():
            names.add(field_name)
            annotation = field.annotation
            for candidate in (annotation, *getattr(annotation, "__args__", ())):
                if hasattr(candidate, "model_fields"):
                    walk(candidate)

    walk(Scope)
    return names


def _source_text() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in SRC.rglob("*.py")
        if p.name not in DECLARATIONS
    )


@pytest.mark.parametrize("field", sorted(_scope_fields()))
def test_config_field_is_consumed_somewhere(field):
    if field in DOCUMENTED_AS_METADATA:
        pytest.skip(f"{field} is recorded for provenance, not behavior")
    if field in ENFORCED_AT_VALIDATION:
        # Prove the refusal is real rather than taking the exemption on trust.
        with pytest.raises(Exception):
            Scope.model_validate({"safety": {field: True}})
        return
    assert re.search(rf"\b{re.escape(field)}\b", _source_text()), (
        f"Scope config field {field!r} is declared but referenced nowhere "
        f"outside config.py — either wire it up or remove it. A toggle that "
        f"silently does nothing is worse than no toggle."
    )
