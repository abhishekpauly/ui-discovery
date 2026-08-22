"""G4 — retention: delete captures past their age, and nothing else.

This is the only command in the engine that destroys data, and the data is
screenshots of somebody's authenticated internal screens. `G3` is explicit that
the redaction guarantees cover text and not pixels, so a stale capture folder is
an indefinite copy of customer data — which is what makes retention worth
having, and what makes getting it wrong expensive.

Four properties these tests exist to hold:

  * **Only captures are ever touched.** A folder is a capture iff it contains
    `run.json`. Everything else in an output root belongs to somebody.
  * **Age comes from the manifest, never the filesystem.** A capture whose age
    cannot be established is kept and reported, never guessed at.
  * **Listing is the default.** `--delete` is required to remove anything, and
    the listing is provably the same set the delete would take.
  * **`runs.jsonl` survives.** It is append-only history; pruning a folder does
    not rewrite the record that the run happened.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ui_discovery.config import Scope
from ui_discovery.prune import (
    RETENTION_OFF,
    find_captures,
    plan,
    prune_captures,
    read_capture,
    render,
)

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def _capture(root, name, *, age_days=None, finished_at="__auto__",
             target="https://acme.test/", extra_files=("report.html",)):
    """Write a capture folder that `prune` should recognise."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": name, "target": target, "outcome": "ok"}
    if finished_at == "__auto__":
        when = NOW - timedelta(days=age_days if age_days is not None else 0)
        manifest["finished_at"] = when.isoformat()
    elif finished_at is not None:
        manifest["finished_at"] = finished_at
    (folder / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    for extra in extra_files:
        (folder / extra).write_text("x" * 1024, encoding="utf-8")
    return folder


# --- only captures are ever touched ------------------------------------------

def test_a_folder_without_a_manifest_is_not_a_capture(tmp_path):
    (tmp_path / "holiday-photos").mkdir()
    (tmp_path / "holiday-photos" / "beach.png").write_text("x", encoding="utf-8")
    assert read_capture(tmp_path / "holiday-photos") is None
    assert find_captures(tmp_path) == []


def test_unrelated_folders_survive_a_delete(tmp_path):
    """The failure that would matter most: an output root shared with anything
    else. Nothing without a manifest may be counted, let alone removed."""
    _capture(tmp_path, "old-capture", age_days=90)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.md").write_text("keep me", encoding="utf-8")
    (tmp_path / "runs.jsonl").write_text('{"run_id": "x"}\n', encoding="utf-8")

    result = prune_captures(tmp_path, 30, delete=True, now=NOW)

    assert [c.name for c in result.removed] == ["old-capture"]
    assert (tmp_path / "notes" / "todo.md").read_text(encoding="utf-8") == "keep me"
    assert not (tmp_path / "old-capture").exists()


def test_the_run_index_is_not_rewritten(tmp_path):
    """`runs.jsonl` is append-only history (principle #4). The index records
    that a run happened; the folder is merely its artifact. Rewriting it to
    hide a pruned run would destroy the trend O5 exists for."""
    _capture(tmp_path, "old", age_days=90)
    index = tmp_path / "runs.jsonl"
    index.write_text('{"run_id": "old", "folder": "old"}\n', encoding="utf-8")

    prune_captures(tmp_path, 30, delete=True, now=NOW)

    assert index.exists()
    assert json.loads(index.read_text(encoding="utf-8").strip())["run_id"] == "old"


# --- age comes from the manifest ---------------------------------------------

def test_age_is_read_from_the_manifest(tmp_path):
    _capture(tmp_path, "recent", age_days=3)
    _capture(tmp_path, "ancient", age_days=400)
    by_name = {c.name: c for c in find_captures(tmp_path, now=NOW)}
    assert by_name["recent"].age_days == pytest.approx(3, abs=0.01)
    assert by_name["ancient"].age_days == pytest.approx(400, abs=0.01)


@pytest.mark.parametrize("manifest_body, expect", [
    ("not json at all", "unreadable"),
    ('["a list"]', "not an object"),
    ('{"run_id": "x"}', "no readable timestamp"),
    ('{"run_id": "x", "finished_at": "yesterday"}', "no readable timestamp"),
])
def test_a_capture_whose_age_cannot_be_established_is_kept(tmp_path, manifest_body, expect):
    """Guessing an age is how you delete the wrong week. These are kept, and —
    just as importantly — *reported*, because a capture nobody can date is one
    retention would otherwise let accumulate in silence."""
    folder = tmp_path / "odd"
    folder.mkdir()
    (folder / "run.json").write_text(manifest_body, encoding="utf-8")

    capture = read_capture(folder, now=NOW)
    assert capture is not None and capture.age_days is None
    assert expect in capture.undetermined

    result = prune_captures(tmp_path, 1, delete=True, now=NOW)
    assert result.expired == [] and len(result.undetermined) == 1
    assert folder.exists()
    assert any("age undetermined" in line for line in render(result, deleted=True))


def test_a_naive_timestamp_is_treated_as_utc(tmp_path):
    """Older manifests carry no offset. Refusing them would strand exactly the
    old captures retention exists for."""
    naive = (NOW - timedelta(days=100)).replace(tzinfo=None).isoformat()
    _capture(tmp_path, "old", finished_at=naive)
    capture = find_captures(tmp_path, now=NOW)[0]
    assert capture.age_days == pytest.approx(100, abs=0.01)


def test_started_at_is_used_when_a_run_never_finished(tmp_path):
    folder = tmp_path / "crashed"
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps({
        "run_id": "crashed",
        "started_at": (NOW - timedelta(days=50)).isoformat(),
        "outcome": "failed",
    }), encoding="utf-8")
    assert read_capture(folder, now=NOW).age_days == pytest.approx(50, abs=0.01)


# --- the retention rule itself -----------------------------------------------

def test_retention_is_off_by_default():
    """A capture is somebody's deliverable. An engine that started deleting them
    because a config gained a key would be worse than one that never deletes."""
    assert Scope().outputs.retention_days == RETENTION_OFF


def test_retention_off_expires_nothing(tmp_path):
    _capture(tmp_path, "ancient", age_days=9999)
    result = prune_captures(tmp_path, RETENTION_OFF, delete=True, now=NOW)
    assert result.expired == [] and (tmp_path / "ancient").exists()
    assert "Retention is off" in "\n".join(render(result, deleted=True))


def test_the_boundary_keeps_a_capture_exactly_at_the_limit(tmp_path):
    """Older *than* the retention, not older than or equal. A capture on its
    last day is inside the window it was promised."""
    _capture(tmp_path, "exactly", age_days=30)
    _capture(tmp_path, "just-over", age_days=30.5)
    result = plan(tmp_path, 30, now=NOW)
    assert [c.name for c in result.expired] == ["just-over"]
    assert [c.name for c in result.kept] == ["exactly"]


def test_dated_layout_captures_are_found(tmp_path):
    """`keep_history` writes `<root>/<date>/<product>`, so the scan has to see
    one level deeper — and no deeper than that."""
    _capture(tmp_path / "2026-01-01", "acme", age_days=200)
    _capture(tmp_path, "acme", age_days=1)
    assert {c.name for c in find_captures(tmp_path, now=NOW)} == {"acme"}
    assert len(find_captures(tmp_path, now=NOW)) == 2


def test_the_scan_does_not_wander_deeper_than_the_two_layouts(tmp_path):
    """An unbounded walk sounds helpful right up until the root is a home
    directory."""
    _capture(tmp_path / "a" / "b" / "c", "buried", age_days=500)
    assert find_captures(tmp_path, now=NOW) == []


# --- listing is the default --------------------------------------------------

def test_planning_deletes_nothing(tmp_path):
    _capture(tmp_path, "old", age_days=90)
    result = plan(tmp_path, 30, now=NOW)
    assert [c.name for c in result.expired] == ["old"]
    assert result.removed == []
    assert (tmp_path / "old").exists()


def test_prune_without_delete_deletes_nothing(tmp_path):
    _capture(tmp_path, "old", age_days=90)
    result = prune_captures(tmp_path, 30, delete=False, now=NOW)
    assert [c.name for c in result.expired] == ["old"]
    assert result.removed == []
    assert (tmp_path / "old").exists()
    lines = "\n".join(render(result, deleted=False))
    assert "would remove" in lines
    assert "--delete" in lines


def test_the_listing_is_the_same_set_the_delete_takes(tmp_path):
    """The report a person reads has to be the set that would actually go —
    two separate calculations could differ, and this is the one place that
    would matter."""
    for name, age in (("a", 90), ("b", 60), ("c", 1)):
        _capture(tmp_path, name, age_days=age)

    listed = {c.name for c in plan(tmp_path, 30, now=NOW).expired}
    removed = {c.name for c in prune_captures(
        tmp_path, 30, delete=True, now=NOW).removed}
    assert listed == removed == {"a", "b"}
    assert (tmp_path / "c").exists()


# --- the CLI -----------------------------------------------------------------

def test_cli_lists_without_deleting(tmp_path, capsys):
    from ui_discovery.prune import main

    _capture(tmp_path, "old", age_days=90)
    assert main([str(tmp_path), "--days", "30"]) == 0
    assert (tmp_path / "old").exists(), "the CLI deleted without --delete"
    assert "would remove" in capsys.readouterr().out


def test_cli_deletes_only_when_asked(tmp_path, capsys):
    from ui_discovery.prune import main

    _capture(tmp_path, "old", age_days=90)
    assert main([str(tmp_path), "--days", "30", "--delete"]) == 0
    assert not (tmp_path / "old").exists()
    assert "removed" in capsys.readouterr().out


def test_cli_refuses_a_negative_retention(tmp_path, capsys):
    from ui_discovery.prune import main

    assert main([str(tmp_path), "--days", "-1"]) == 2
    assert "zero or positive" in capsys.readouterr().err


def test_cli_reads_retention_from_a_config(tmp_path, capsys):
    from ui_discovery.prune import main

    _capture(tmp_path, "old", age_days=90)
    config = tmp_path / "scope.yaml"
    config.write_text("outputs:\n  retention_days: 30\n", encoding="utf-8")
    assert main([str(tmp_path), "--config", str(config)]) == 0
    assert "would remove" in capsys.readouterr().out


def test_a_missing_root_is_not_an_error(tmp_path, capsys):
    """Nothing to prune is a fine outcome, not a failure — a scheduled prune
    against a machine that has not captured yet must not exit non-zero."""
    from ui_discovery.prune import main

    assert main([str(tmp_path / "nope"), "--days", "30"]) == 0


# --- the library surface -----------------------------------------------------

def test_available_through_the_public_api(tmp_path):
    import ui_discovery

    _capture(tmp_path, "old", age_days=90)
    assert [c.name for c in ui_discovery.find_captures(tmp_path)] == ["old"]
    assert ui_discovery.prune_captures(tmp_path, 30, now=NOW).expired


# --- against a manifest the engine really wrote ------------------------------

def test_a_real_capture_is_recognised_and_datable(serve, tmp_path):
    """Every test above hands `prune` a manifest this file wrote, which cannot
    prove the two agree. This runs the pipeline and prunes its output, so the
    shape being read is the shape the engine actually writes."""
    from ui_discovery.pipeline import main as pipeline_main

    site = serve("fixtures/site")
    assert pipeline_main([site.url("index.html"), "--output", str(tmp_path),
                          "--max-pages", "2", "--headless"]) == 0

    captures = find_captures(tmp_path)
    assert len(captures) == 1, f"expected one capture, found {captures}"
    capture = captures[0]
    assert capture.run_id, "run_id not read from a real manifest"
    assert capture.age_days is not None, capture.undetermined
    assert capture.age_days < 1
    assert capture.size_bytes > 0

    # Fresh, so retention keeps it...
    assert prune_captures(tmp_path, 30).expired == []
    assert capture.path.exists()

    # ...and the run index beside it is never mistaken for a capture.
    assert (tmp_path / "runs.jsonl").exists()
    assert all(c.path.is_dir() for c in captures)


def test_a_real_capture_past_its_retention_is_removed(serve, tmp_path):
    from ui_discovery.pipeline import main as pipeline_main

    site = serve("fixtures/site")
    assert pipeline_main([site.url("index.html"), "--output", str(tmp_path),
                          "--max-pages", "2", "--headless"]) == 0

    folder = find_captures(tmp_path)[0].path
    later = datetime.now(timezone.utc) + timedelta(days=90)
    result = prune_captures(tmp_path, 30, delete=True, now=later)

    assert [c.path for c in result.removed] == [folder]
    assert not folder.exists()
    assert (tmp_path / "runs.jsonl").exists(), "the index was destroyed with the capture"
