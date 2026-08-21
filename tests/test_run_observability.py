"""A run has to be able to account for itself.

The engine could always say what it found. It could not say who ran it, against
what, under whose authorization, how long each stage took, or what happened on
the way. `O1`-`O5` are that record: a run id, an ordered event stream, a
manifest, the metrics derived from it, and an index of every run so far.

Three properties these tests exist to hold:

  * **The record survives failure.** A run that dies is precisely the run whose
    record you want, so events are flushed as they happen and a crashing stage
    still produces a well-formed trailing entry.
  * **The record never contains a secret.** Manifests get pasted into tickets.
  * **The record is measured, not remembered.** "Probing every page is fine" is
    an impression until a number says so.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ui_discovery.crawler import crawl_site
from ui_discovery.inventory import METRICS_HEADING, attach_metrics, write_inventory
from ui_discovery.models import RunManifest
from ui_discovery.run import (
    RunContext,
    command_line,
    config_digest,
    new_run_id,
    read_events,
    read_index,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _tiny_crawl(tmp_path):
    """A one-screen crawl, built in memory. The metrics block is spliced into
    `summary.md` by shape, not by content, so paying for a browser to produce
    one would buy nothing."""
    from ui_discovery.models import Crawl, CrawlConfig, CrawlStats, Page, PageNode

    url = "http://x.test/index.html"
    page = Page(schema_version="0", engine_version="0", extracted_at="now",
                requested_url=url, final_url=url, title="Home")
    return Crawl(
        schema_version="0", engine_version="0", crawl_id="x",
        started_at="a", finished_at="b",
        config=CrawlConfig(start_url=url, max_pages=1, max_depth=1,
                           strategy="same-domain"),
        stats=CrawlStats(pages_crawled=1, pages_failed=0, unique_urls=1,
                         links_discovered=0, runtime_seconds=0.0),
        pages=[PageNode(url=url, page=page)],
    )


# --- O1: run identity -------------------------------------------------------

def test_a_run_id_is_unique_and_shaped_like_a_crawl_id():
    """Twelve hex characters, matching `crawl_id`, so the two read as siblings
    in a log rather than as different kinds of thing."""
    ids = {new_run_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 12 and all(c in "0123456789abcdef" for c in i)
               for i in ids)


def test_every_event_carries_the_run_id(tmp_path):
    with RunContext.begin(str(tmp_path), target="https://x.test/") as run:
        run.emit("page.captured", url="a")
        run.emit("page.captured", url="b")
    events = read_events(str(tmp_path))
    assert events
    assert {e.run_id for e in events} == {run.run_id}


def test_the_crawl_records_which_run_produced_it(tmp_path):
    from tests.conftest import Server

    server = Server(FIXTURES / "forms")
    try:
        with RunContext.begin(str(tmp_path), target=server.base) as run:
            crawl = asyncio.run(crawl_site(
                f"{server.base}/index.html", max_pages=2, max_depth=0,
                output_dir=str(tmp_path), probe=False, screenshots=False,
                run=run))
    finally:
        server.stop()
    assert crawl.run_id == run.run_id


def test_a_crawl_without_a_run_is_still_a_complete_artifact(tmp_path):
    """`crawl` invoked directly leaves no trail, and that is not a degraded
    crawl — the run is a pipeline concept."""
    from tests.conftest import Server

    server = Server(FIXTURES / "site")
    try:
        crawl = asyncio.run(crawl_site(
            f"{server.base}/index.html", max_pages=2, max_depth=0,
            output_dir=str(tmp_path), probe=False, screenshots=False))
    finally:
        server.stop()
    assert crawl.run_id is None
    assert crawl.pages
    assert not (tmp_path / "events.jsonl").exists()


# --- O2: the event stream ---------------------------------------------------

def test_events_are_ordered_and_bracketed(tmp_path):
    with RunContext.begin(str(tmp_path), target="x") as run:
        with run.stage("crawl"):
            run.emit("page.captured", stage="crawl", url="a")
    events = read_events(str(tmp_path))
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[0].event == "run.started"
    assert events[-1].event == "run.finished"


def test_one_object_per_line(tmp_path):
    """`events.jsonl` is meant to be grepped and tailed. One event per line is
    the whole contract."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        for i in range(5):
            run.emit("page.captured", url=f"page-{i}")
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 7
    for line in lines:
        assert json.loads(line)["run_id"] == run.run_id


def test_a_failing_stage_still_produces_a_well_formed_record(tmp_path):
    run = RunContext.begin(str(tmp_path), target="x")
    with pytest.raises(ValueError):
        with run.stage("docgen"):
            raise ValueError("boom")
    run.finish()

    events = read_events(str(tmp_path))
    finished = [e for e in events if e.event == "stage.finished"]
    assert finished and finished[-1].data.get("status") == "failed"
    assert finished[-1].level == "error"
    assert "boom" in finished[-1].message

    manifest = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert manifest["outcome"] == "partial"
    assert manifest["failed_stages"] == ["docgen"]


def test_a_run_that_raises_is_still_recorded(tmp_path):
    """The run whose record matters most is the one that died."""
    with pytest.raises(RuntimeError):
        with RunContext.begin(str(tmp_path), target="x") as run:
            run.emit("page.captured", url="a")
            raise RuntimeError("the crawl exploded")

    events = read_events(str(tmp_path))
    assert events[-1].event in ("run.failed", "run.finished")
    assert any(e.event == "run.failed" for e in events)
    manifest = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert manifest["outcome"] == "failed"


def test_a_skipped_stage_is_a_fact_not_a_silence(tmp_path):
    """"We skipped qagen" and "qagen produced nothing" are different, and only
    one of them is about your application."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.skipped("qagen", "excluded with --skip")
    manifest = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    qagen = next(s for s in manifest["stages"] if s["name"] == "qagen")
    assert qagen["status"] == "skipped"
    assert "--skip" in qagen["error"]


def test_a_payload_key_cannot_collide_with_the_event_name(tmp_path):
    """The crawler emits `state.captured` with the state's own name. When the
    event name was a normal keyword, that was a TypeError — and `emit` never
    raises, so it silently dropped every one of those events. Positional-only
    is what stops it recurring."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.emit("state.captured", name="Model Playground", event="not-the-name")
    captured = [e for e in read_events(str(tmp_path))
                if e.event == "state.captured"]
    assert len(captured) == 1
    assert captured[0].data["name"] == "Model Playground"


def test_events_are_flushed_as_they_happen(tmp_path):
    """Buffering to the end would lose exactly the run you care about."""
    run = RunContext.begin(str(tmp_path), target="x")
    run.emit("page.captured", url="a")
    # Nothing has finished; the events must already be on disk.
    assert len(read_events(str(tmp_path))) == 2


def test_a_truncated_final_line_is_tolerated(tmp_path):
    """What a killed process leaves behind."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.emit("page.captured", url="a")
    path = tmp_path / "events.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"run_id": "trunc',
                    encoding="utf-8")
    assert read_events(str(tmp_path))  # reads what it can, drops the fragment


# --- O3: the manifest -------------------------------------------------------

def test_the_manifest_validates_and_carries_the_run(tmp_path):
    with RunContext.begin(str(tmp_path), target="https://x.test/") as run:
        run.crawl_id = "abc123def456"
        with run.stage("crawl"):
            pass
    m = RunManifest.model_validate(
        json.loads((tmp_path / "run.json").read_text(encoding="utf-8")))
    assert m.run_id == run.run_id
    assert m.crawl_id == "abc123def456"
    assert m.target == "https://x.test/"
    assert m.outcome == "ok"
    assert m.operator and m.host


def test_stage_durations_roughly_sum_to_the_run(tmp_path):
    with RunContext.begin(str(tmp_path), target="x") as run:
        for name in ("crawl", "analyze"):
            with run.stage(name):
                pass
    m = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    total = sum(s["duration_ms"] for s in m["stages"])
    assert total <= m["duration_ms"] + 50


def test_the_config_digest_is_stable_and_sensitive():
    """Two runs are provably the same configuration, and differ the moment one
    setting does."""
    a = {"budget": {"max_pages": 25}, "probe": {"tabs": "all"}}
    reordered = {"probe": {"tabs": "all"}, "budget": {"max_pages": 25}}
    changed = {"budget": {"max_pages": 26}, "probe": {"tabs": "all"}}

    assert config_digest(a) == config_digest(reordered)
    assert config_digest(a) != config_digest(changed)
    assert len(config_digest(a)) == 64


def test_the_digest_covers_a_resolved_scope():
    from ui_discovery.config import Scope

    base = Scope()
    tweaked = Scope.model_validate({"budget": {"max_pages": 99}})
    assert config_digest(base) != config_digest(tweaked)


def test_the_event_count_matches_the_file(tmp_path):
    """A manifest claiming N events beside a file holding N+1 is a small lie
    that costs a reader real time."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        for i in range(4):
            run.emit("page.captured", url=str(i))
    m = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert m["event_count"] == len(read_events(str(tmp_path)))


def test_the_manifest_lists_what_the_run_actually_wrote(tmp_path):
    with RunContext.begin(str(tmp_path), target="x"):
        (tmp_path / "report.md").write_text("hello", encoding="utf-8")
        (tmp_path / "screenshots").mkdir()
        (tmp_path / "screenshots" / "a.png").write_bytes(b"x")
    m = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert "report.md" in m["artifacts"]
    assert "screenshots/a.png" in m["artifacts"]
    assert "run.json" not in m["artifacts"]


# --- O4: where the time went ------------------------------------------------

def test_the_manifest_says_where_the_time_went(tmp_path):
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.record_stats(pages_crawled=4)
        for name in ("crawl", "analyze"):
            with run.stage(name):
                pass
    m = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    metrics = m["metrics"]
    assert set(metrics["stage_ms"]) == {"crawl", "analyze"}
    assert metrics["pages"] == 4
    assert metrics["slowest_stage"] in ("crawl", "analyze")


def test_the_stage_shares_and_the_gap_between_them_account_for_the_run(tmp_path):
    """The pipeline writes reports and the inventory *between* stages. If that
    time went unreported the shares would quietly sum to less than the run, and
    a reader would go looking for a stage that does not exist."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        for name in ("crawl", "analyze", "docgen"):
            with run.stage(name):
                pass
    metrics = json.loads(
        (tmp_path / "run.json").read_text(encoding="utf-8"))["metrics"]
    accounted = sum(metrics["stage_ms"].values()) + metrics["outside_stages_ms"]
    assert abs(accounted - metrics["total_ms"]) <= 50


def test_a_stage_records_what_it_produced(tmp_path):
    """A duration alone says a stage was slow. A duration beside a count says
    whether it was slow for a reason."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        with run.stage("analyze"):
            run.count(unique_elements=340, shared_components=12)
    m = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    analyze = next(s for s in m["stages"] if s["name"] == "analyze")
    assert analyze["counts"] == {"unique_elements": 340, "shared_components": 12}
    assert m["metrics"]["counts"]["analyze"]["unique_elements"] == 340


def test_counting_outside_a_stage_is_harmless(tmp_path):
    """A count is a nicety. Losing a capture because one was recorded in the
    wrong place would be an absurd trade."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.count(pages=3)                      # no stage is open
    assert json.loads(
        (tmp_path / "run.json").read_text(encoding="utf-8"))["outcome"] == "ok"


def test_per_screen_cost_is_derived_not_asserted(tmp_path):
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.record_stats(pages_crawled=10)
        with run.stage("crawl"):
            pass
        run.stages[-1].duration_ms = 20_000     # a crawl worth measuring
    metrics = json.loads(
        (tmp_path / "run.json").read_text(encoding="utf-8"))["metrics"]
    assert metrics["ms_per_page"] == 2000
    assert metrics["pages_per_minute"] == 30.0


def test_the_probe_share_of_the_crawl_is_reported(tmp_path):
    """`QA.3` — "is probing every page too slow?" — answered from data."""
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.record_stats(pages_crawled=2, probe_ms=6000)
        with run.stage("crawl"):
            pass
        run.stages[-1].duration_ms = 10_000
    metrics = json.loads(
        (tmp_path / "run.json").read_text(encoding="utf-8"))["metrics"]
    assert metrics["probe_ms"] == 6000
    assert metrics["probe_share_of_crawl_pct"] == 60.0


def test_the_crawl_measures_what_interacting_cost(tmp_path):
    """Measured in the crawler, so a `crawl` invoked directly can answer the
    question too — the run is where it is *reported*, not where it is timed."""
    from tests.conftest import Server

    server = Server(FIXTURES / "interactive")
    try:
        probed = asyncio.run(crawl_site(
            f"{server.base}/index.html", max_pages=1, max_depth=0,
            output_dir=str(tmp_path / "with"), probe=True, screenshots=False))
        plain = asyncio.run(crawl_site(
            f"{server.base}/index.html", max_pages=1, max_depth=0,
            output_dir=str(tmp_path / "without"), probe=False,
            screenshots=False))
    finally:
        server.stop()
    assert probed.stats.probe_ms > 0
    assert plain.stats.probe_ms == 0


def test_the_summary_says_where_the_time_went(tmp_path):
    """The metrics land in `summary.md`, which is the file a person opens —
    `run.json` is where they go once they want the detail."""
    crawl = _tiny_crawl(tmp_path)
    write_inventory(crawl, str(tmp_path))
    before = (tmp_path / "summary.md").read_text(encoding="utf-8")

    with RunContext.begin(str(tmp_path), target="x") as run:
        run.record_stats(pages_crawled=1, probe_ms=1500)
        with run.stage("crawl"):
            run.count(pages=1)
    manifest = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert attach_metrics(manifest, str(tmp_path))

    after = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert METRICS_HEADING in after
    assert run.run_id in after
    # Splicing must not cost the reader anything that was already there.
    for section in ("## Elements by kind", "## Screens", "## Files in this folder"):
        assert section in after and section in before


def test_the_metrics_block_replaces_itself_rather_than_accumulating(tmp_path):
    """Re-running into the same folder is ordinary. Two timing tables, one of
    them stale, is not."""
    crawl = _tiny_crawl(tmp_path)
    write_inventory(crawl, str(tmp_path))
    with RunContext.begin(str(tmp_path), target="x") as run:
        with run.stage("crawl"):
            pass
    manifest = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    attach_metrics(manifest, str(tmp_path))
    attach_metrics(manifest, str(tmp_path))

    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert text.count(METRICS_HEADING) == 1
    assert text.count("## Files in this folder") == 1


def test_a_missing_summary_is_not_a_failed_capture(tmp_path):
    assert attach_metrics({"run_id": "x", "metrics": {}}, str(tmp_path)) is None


# --- O5: the run index ------------------------------------------------------

def test_one_line_per_run(tmp_path):
    root = tmp_path / "captures"
    for i in range(3):
        with RunContext.begin(str(root / f"run-{i}"), target="https://x.test/",
                              index_dir=str(root)) as run:
            run.record_stats(pages_crawled=i)
    rows = read_index(str(root))
    assert len(rows) == 3
    assert len({r["run_id"] for r in rows}) == 3
    assert [r["pages"] for r in rows] == [0, 1, 2]


def test_finishing_twice_indexes_once(tmp_path):
    """A caller that finishes explicitly inside a `with` block passes through
    `__exit__` too. A run counted twice would corrupt exactly the trend the
    index exists to show."""
    root = tmp_path / "captures"
    with RunContext.begin(str(root / "one"), target="x",
                          index_dir=str(root)) as run:
        run.finish()
    assert len(read_index(str(root))) == 1


def test_the_index_carries_what_you_would_scan_down(tmp_path):
    root = tmp_path / "captures"
    with RunContext.begin(str(root / "acme-portal"), target="https://acme.test/",
                          index_dir=str(root)) as run:
        run.crawl_id = "abc123def456"
        run.describe(config_sha256="deadbeef")
        run.record_stats(pages_crawled=7, elements=250)
        with run.stage("crawl"):
            pass
    row = read_index(str(root))[0]
    assert row["target"] == "https://acme.test/"
    assert row["folder"] == "acme-portal"      # where to go and read the rest
    assert row["outcome"] == "ok"
    assert row["pages"] == 7 and row["elements"] == 250
    assert row["crawl_id"] == "abc123def456"
    assert row["config_sha256"] == "deadbeef"
    assert row["at"] and row["engine_version"]


def test_the_index_records_a_run_that_went_wrong(tmp_path):
    """A run that failed is the one you most want to find later."""
    root = tmp_path / "captures"
    run = RunContext.begin(str(root / "one"), target="x", index_dir=str(root))
    with pytest.raises(ValueError):
        with run.stage("docgen"):
            raise ValueError("boom")
    run.finish()
    row = read_index(str(root))[0]
    assert row["outcome"] == "partial"
    assert row["failed_stages"] == ["docgen"]


def test_the_index_defaults_to_the_folder_above_the_capture(tmp_path):
    """An index inside the folder it indexes would hold one line and answer
    nothing."""
    with RunContext.begin(str(tmp_path / "capture"), target="x"):
        pass
    assert (tmp_path / "runs.jsonl").exists()
    assert not (tmp_path / "capture" / "runs.jsonl").exists()


def test_the_index_stays_readable_line_by_line(tmp_path):
    """Readable after N runs without loading any single large file — the whole
    reason this is not a database (`X6`)."""
    root = tmp_path / "captures"
    for i in range(25):
        with RunContext.begin(str(root / f"r{i}"), target="x",
                              index_dir=str(root)):
            pass
    lines = (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 25
    assert all(json.loads(line)["run_id"] for line in lines)


def test_the_index_never_carries_a_secret(tmp_path):
    root = tmp_path / "captures"
    with RunContext.begin(str(root / "one"), target="x",
                          index_dir=str(root)) as run:
        run.describe(auth_used=True, auth_source="refreshToken for x.test")
    blob = (root / "runs.jsonl").read_text(encoding="utf-8")
    for secret in ("cookie", "refreshToken", "Bearer ", "eyJ", "password"):
        assert secret not in blob


# --- the record must never carry a secret -----------------------------------

def test_the_manifest_records_auth_posture_but_never_the_session(tmp_path):
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.describe(auth_used=True, auth_source="refreshToken for x.test",
                     auth_expires_in_hours=124.7)
    blob = (tmp_path / "run.json").read_text(encoding="utf-8")
    m = json.loads(blob)
    assert m["auth_used"] is True
    assert m["auth_expires_in_hours"] == 124.7
    for secret in ("cookie", "refresh_token", "Bearer ", "eyJ"):
        assert secret not in blob


def test_the_command_line_does_not_advertise_where_credentials_live(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["pipeline.py", "https://x.test/", "--auth-state",
         "/home/someone/secrets/prod-session.json", "--headless"])
    line = command_line()
    assert "prod-session.json" in line     # which session, usefully
    assert "/home/someone/secrets" not in line   # not where it lives


def test_events_never_carry_a_session(tmp_path):
    with RunContext.begin(str(tmp_path), target="x") as run:
        run.describe(auth_used=True)
        run.emit("auth.rejected", url="x", signal="login-url")
    blob = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    for secret in ("cookie", "Bearer ", "eyJ", "password"):
        assert secret not in blob


# --- against a real crawl ---------------------------------------------------

@pytest.fixture(scope="module")
def probed_run(tmp_path_factory):
    """A real crawl with the probe on, so the page-level events have something
    to report."""
    from tests.conftest import Server

    out = tmp_path_factory.mktemp("run")
    server = Server(FIXTURES / "interactive")
    try:
        with RunContext.begin(str(out), target=server.base) as run:
            crawl = asyncio.run(crawl_site(
                f"{server.base}/index.html", max_pages=2, max_depth=0,
                output_dir=str(out), probe=True, run=run))
    finally:
        server.stop()
    return crawl, out, run


def test_a_crawl_reports_the_pages_it_captured(probed_run):
    _, out, _ = probed_run
    captured = [e for e in read_events(str(out)) if e.event == "page.captured"]
    assert captured
    assert all(e.data.get("url") for e in captured)
    assert any(e.data.get("elements", 0) > 0 for e in captured)


def test_a_crawl_reports_what_it_refused_and_why(probed_run):
    """"We did not click Delete account" is a claim a capture should be able to
    substantiate."""
    _, out, _ = probed_run
    refused = [e for e in read_events(str(out)) if e.event == "probe.refused"]
    assert refused
    delete = [e for e in refused if e.data.get("target") == "Delete account"]
    assert delete, [e.data.get("target") for e in refused]
    assert delete[0].data["verdict"] == "BLOCK"
    assert delete[0].data["reason"]


def test_a_crawl_reports_the_states_it_opened(probed_run):
    """This is the assertion that would have caught the keyword collision:
    `states_captured` was non-zero while not one `state.captured` event was
    ever written."""
    crawl, out, _ = probed_run
    expected = sum(len(n.probe.states) for n in crawl.pages if n.probe)
    assert expected > 0, "the fixture should reveal at least one state"
    events = [e for e in read_events(str(out)) if e.event == "state.captured"]
    assert len(events) == expected
    assert all(e.data.get("kind") for e in events)
    assert any(e.data.get("state_name") or e.data.get("trigger")
               for e in events)
