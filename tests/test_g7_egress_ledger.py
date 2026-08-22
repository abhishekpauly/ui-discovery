"""G7 — the egress ledger.

`CLAUDE.md` principle #11 says the engine talks to nothing but the target. That
is a design claim, and every other claim a capture makes is evidenced — the
safety envelope, the data-handling posture, the authorization. This one was
not, so a reader had to take it on trust, and a regression that started
fetching a third-party asset would have been invisible in the artifact.

It matters more from here on: `M1` will start fetching sitemaps and `H7` will
start recording links that leave the product, so "which hosts did this run
talk to?" stops being a question with an obvious answer.

Two properties are worth more than the rollup itself:

  * **Present on every run.** A ledger that appeared only when something
    unusual happened would say nothing about the runs where it was absent.
    Unlike `safety` and `data_handling`, it is never `None`.
  * **Independent of probing.** The richer per-request record only exists when
    the probe is on. A ledger that inherited that condition would answer a
    different question than the one it advertises.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.models import EgressLedger, RunManifest
from ui_discovery.network import build_ledger, host_of
from ui_discovery.run import RunContext

# --- the rollup, without a browser ------------------------------------------


def test_host_of_reads_host_and_port():
    assert host_of("http://Example.test:8080/a/b?x=1") == "example.test:8080"
    assert host_of("https://example.test/") == "example.test"
    assert host_of("not a url") == ""


def test_the_ledger_names_the_target_and_counts_its_requests():
    ledger = build_ledger(
        ["http://a.test/", "http://a.test/style.css", "http://a.test/x.png"],
        "http://a.test/")
    EgressLedger.model_validate(ledger)
    assert ledger["target_host"] == "a.test"
    assert [h["host"] for h in ledger["hosts"]] == ["a.test"]
    assert ledger["hosts"][0]["requests"] == 3
    assert ledger["hosts"][0]["first_path"] == "/"
    assert ledger["off_scope"] == []


def test_a_foreign_host_is_flagged_and_sorted_last():
    """Loudly, per the spec — a reader should not have to filter the list to
    discover that the engine talked to someone else."""
    ledger = build_ledger(
        ["http://a.test/", "https://cdn.other/p.png", "http://a.test/x"],
        "http://a.test/")
    assert ledger["off_scope"] == ["cdn.other"]
    assert [h["host"] for h in ledger["hosts"]] == ["a.test", "cdn.other"]
    assert ledger["hosts"][1]["in_scope"] is False
    assert ledger["hosts"][1]["first_path"] == "/p.png"


def test_scope_is_exact_host_match():
    """`same-host` is the engine's only subdomain policy today. A ledger that
    called `cdn.target.test` in-scope would be claiming something the crawler
    does not believe — `H6` is the item that widens this."""
    ledger = build_ledger(["https://cdn.target.test/x"], "https://target.test/")
    assert ledger["off_scope"] == ["cdn.target.test"]


def test_a_port_makes_a_different_host():
    ledger = build_ledger(["http://a.test:9000/x"], "http://a.test:8000/")
    assert ledger["off_scope"] == ["a.test:9000"]


def test_unparseable_urls_are_skipped_not_counted():
    ledger = build_ledger(["", "not a url", "http://a.test/x"], "http://a.test/")
    assert ledger["total_requests"] == 1


def test_an_empty_ledger_still_names_the_target():
    """The quiet case is a result, not a gap."""
    ledger = build_ledger([], "http://a.test/")
    assert ledger["target_host"] == "a.test"
    assert ledger["hosts"] == []
    assert ledger["total_requests"] == 0


# --- on the manifest --------------------------------------------------------


def test_every_manifest_carries_a_ledger_even_when_nothing_ran(tmp_path):
    """A run that died before the crawl still has to say what it contacted.
    `safety` and `data_handling` are nullable on purpose; this is not."""
    run = RunContext.begin(str(tmp_path), target="http://a.test/")
    manifest = run.manifest()
    assert isinstance(manifest.egress, EgressLedger)
    assert manifest.egress.hosts == []


def test_a_described_ledger_survives_the_round_trip(tmp_path):
    run = RunContext.begin(str(tmp_path), target="http://a.test/")
    run.describe(egress=build_ledger(
        ["http://a.test/", "https://cdn.other/x"], "http://a.test/"))
    payload = json.loads(run.manifest().model_dump_json())
    assert payload["egress"]["off_scope"] == ["cdn.other"]
    RunManifest.model_validate(payload)


# --- against a real crawl ---------------------------------------------------


def _crawl(url: str, out: Path, run=None, *, max_pages: int = 2,
           max_depth: int = 1, **kw) -> None:
    asyncio.run(crawl_site(
        url, output_dir=str(out), run=run,
        options=CrawlOptions(max_pages=max_pages, max_depth=max_depth,
                             screenshots=False, **kw),
    ))


def test_a_fixture_run_names_exactly_the_fixture_host(serve, tmp_path):
    """Depth 0 on purpose: `second.html` links back to `index.html`, which
    reaches off-site by design, so following it would be testing the other
    case. The quiet ledger is the one worth pinning here."""
    server = serve("fixtures/egress")
    run = RunContext.begin(str(tmp_path), target=server.base)
    _crawl(server.url("second.html"), tmp_path, run=run, probe=False,
           max_pages=1, max_depth=0)

    ledger = run.manifest().egress
    hosts = [h.host for h in ledger.hosts]
    assert hosts == [host_of(server.base)], hosts
    assert ledger.off_scope == []
    assert ledger.total_requests > 0


def test_a_third_party_asset_is_listed_and_flagged(serve, tmp_path):
    """The fixture references `127.0.0.1:1`, which refuses instantly.

    The suite must never depend on an external site — a test that reached one
    would fail offline for reasons that have nothing to do with the engine —
    and an earlier version of this fixture rewrote the asset URL from a query
    parameter so a second local server could stand in for a CDN. CodeQL was
    right to reject that: reading `location.search` into `img.src` is an XSS
    and open-redirect shape whether or not the file is a fixture.

    A refused port needs no server, no DNS and no port discovery, and a host
    the engine *tried* to reach is exactly what the ledger is for — so this
    also covers the `requestfailed` listener, which nothing else exercises.
    """
    target = serve("fixtures/egress")
    run = RunContext.begin(str(tmp_path), target=target.base)
    _crawl(target.url("index.html"), tmp_path, run=run, probe=False)

    ledger = run.manifest().egress
    assert ledger.off_scope == ["127.0.0.1:1"]
    flagged = [h for h in ledger.hosts if not h.in_scope]
    assert [h.host for h in flagged] == ["127.0.0.1:1"]
    assert flagged[0].first_path == "/pixel.png"

    # The target is still listed, in scope, and first.
    assert ledger.hosts[0].host == host_of(target.base)
    assert ledger.hosts[0].in_scope is True


def test_the_ledger_does_not_depend_on_the_probe(serve, tmp_path):
    """The per-request record only exists when probing is on. If the ledger
    inherited that, it would silently be empty on exactly the cheap runs an
    operator schedules."""
    server = serve("fixtures/egress")
    run = RunContext.begin(str(tmp_path), target=server.base)
    _crawl(server.url("second.html"), tmp_path, run=run, probe=False,
           max_pages=1, max_depth=0)
    assert run.manifest().egress.total_requests > 0
