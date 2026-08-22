"""The board sync reads a backlog ID out of an issue title.

That parse is the whole risk in `scripts/sync_board.py`. Everything else it
does is idempotent and visible — but a title it reads wrongly writes a wrong
`Backlog ID` onto a card, and the board is exactly the place nobody re-checks.

Two dash styles are in circulation and both must keep working: issues #1-#14
predate the em-dash convention, and renaming them would break every reference
already written down.

No network and no `gh`: the parse is a pure function, which is why it is worth
testing here rather than discovering on the board.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sync_board import backlog_id  # noqa: E402


@pytest.mark.parametrize("title, expected", [
    # The em-dash convention the issue templates ask for.
    ("M1 — Sitemap ingestion", "M1"),
    ("PV1 — Viewport and device variants", "PV1"),
    ("H11 — TLS verification as a recorded decision", "H11"),
    ("EPIC-WATCH — A capture that runs itself", "EPIC-WATCH"),
    # The hyphen style issues #1-#14 were opened with.
    ("O1 - Run identity", "O1"),
    ("G4 - Retention", "G4"),
    ("QA.4 - The report reviewed as product documentation", "QA.4"),
    ("EPIC-QA - Real-world validation", "EPIC-QA"),
    # A title whose *summary* also contains a dash must not swallow it.
    ("EPIC-OBS - Observability - make a run accountable", "EPIC-OBS"),
    ("EPIC-GOV - Governance - state the rules a capture ran under", "EPIC-GOV"),
    ("W2 — What counts as a change worth waking up for", "W2"),
    # Backticks and punctuation in the summary are none of the parser's business.
    ("M2 — `map` command", "M2"),
    ("T3 — Does the product agree with itself?", "T3"),
    ("X8 — Which command do I run?", "X8"),
])
def test_the_id_is_read_from_the_title(title, expected):
    assert backlog_id(title) == expected


@pytest.mark.parametrize("title", [
    # No separator at all — a title someone typed by hand.
    "Fix the crawler",
    # An en-dash or a hyphen with no spaces is not the convention, and guessing
    # would be worse than declining: the script reports these and moves on.
    "M1-Sitemap ingestion",
    "Sitemap ingestion",
    "",
    "   ",
    # A leading dash is a bullet, not an ID.
    "- M1 — Sitemap ingestion",
])
def test_a_title_without_an_id_is_left_alone(title):
    assert backlog_id(title) is None


def test_every_id_prefix_the_story_template_lists_parses():
    """The template offers these prefixes; all of them must survive the parse."""
    prefixes = ["F0.1", "X9", "H10", "C3", "R1", "S1", "V5.4", "QA.2",
                "O5", "G7", "M4", "L1", "I3", "W1", "PV3", "T2"]
    for prefix in prefixes:
        assert backlog_id(f"{prefix} — a summary") == prefix
