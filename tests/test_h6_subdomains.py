"""H6 — how much of a hostname counts as "the same site".

`util.same_site` compared `netloc` exactly. That is right for a product on one
host and wrong for most real ones: split across `app.example.com` and
`admin.example.com`, a portal captured either as two unrelated targets or — far
more often — as one target with half its modules missing.

**Silently** is what makes it a defect rather than a limitation. A capture that
stops at a subdomain boundary looks exactly like a product that ends there, and
nothing in the output distinguishes the two. Someone reads the report, sees no
admin module, and concludes the product has none.

Three policies, and the default is unchanged, so a config that never mentions
this crawls exactly as it did before.

**On what is tested where.** `registrable-domain` is covered directly rather
than through a crawl, and that is a deliberate limit rather than an omission:
proving it end-to-end needs two hostnames that actually resolve
(`app.example.com`, `admin.example.com`), and the suite must never depend on
DNS or on anything outside the machine. The crawl-level tests therefore use
`list`, which is the policy a real two-host portal is most likely to set
anyway, and the loopback aliases `127.0.0.1` and `localhost` — genuinely two
different hostnames served by one process.
"""

from __future__ import annotations

import asyncio

import pytest

from ui_discovery.config import ScopeRules
from ui_discovery.crawler import CrawlOptions, crawl_site
from ui_discovery.run import RunContext
from ui_discovery.util import (
    HOST_LIST,
    REGISTRABLE_DOMAIN,
    SAME_HOST,
    same_site,
)

ROOT = "https://app.example.com/dashboard"


# --- same-host: today's behaviour, unchanged --------------------------------


def test_same_host_is_exact_netloc():
    assert same_site("https://app.example.com/other", ROOT) is True
    assert same_site("https://admin.example.com/other", ROOT) is False
    assert same_site("https://example.com/other", ROOT) is False


def test_same_host_counts_the_port():
    """Two ports are two hosts under this policy. That is the existing
    contract and a lot of fixtures rely on it."""
    assert same_site("http://127.0.0.1:8001/a", "http://127.0.0.1:8001/b") is True
    assert same_site("http://127.0.0.1:8002/a", "http://127.0.0.1:8001/b") is False


def test_same_host_is_the_default():
    assert same_site("https://admin.example.com/x", ROOT) is False
    assert ScopeRules().subdomains == SAME_HOST


# --- registrable-domain -----------------------------------------------------


def test_registrable_domain_unifies_subdomains():
    for url in ("https://admin.example.com/x", "https://app.example.com/x",
                "https://example.com/x", "https://deep.nested.example.com/x"):
        assert same_site(url, ROOT, REGISTRABLE_DOMAIN) is True, url


def test_registrable_domain_still_refuses_an_unrelated_host():
    for url in ("https://evil.com/x", "https://example.org/x",
                "https://notexample.com/x"):
        assert same_site(url, ROOT, REGISTRABLE_DOMAIN) is False, url


def test_registrable_domain_understands_multi_part_suffixes():
    """`example.co.uk` is the registrable domain; `co.uk` is a public suffix.
    Getting this wrong the naive way — "last two labels" — would treat every
    `.co.uk` site as one company."""
    root = "https://app.example.co.uk/"
    assert same_site("https://admin.example.co.uk/x", root, REGISTRABLE_DOMAIN) is True
    assert same_site("https://other.co.uk/x", root, REGISTRABLE_DOMAIN) is False


def test_registrable_domain_falls_back_to_the_host_for_ips_and_localhost():
    """An IP has no registrable domain. Comparing the empty string would make
    every IP the same site as every other, which is the widening this gate
    exists to prevent."""
    assert same_site("http://127.0.0.1/a", "http://127.0.0.1/b",
                     REGISTRABLE_DOMAIN) is True
    assert same_site("http://127.0.0.2/a", "http://127.0.0.1/b",
                     REGISTRABLE_DOMAIN) is False
    assert same_site("http://localhost/a", "http://127.0.0.1/b",
                     REGISTRABLE_DOMAIN) is False


def test_registrable_domain_ignores_the_port():
    """One product served on two ports of one host is one product, and a port
    is not part of a domain."""
    assert same_site("http://127.0.0.1:9001/a", "http://127.0.0.1:9002/b",
                     REGISTRABLE_DOMAIN) is True


# --- list -------------------------------------------------------------------


def test_list_admits_exactly_the_named_hosts():
    hosts = ("admin.example.com", "reports.example.com")
    assert same_site("https://admin.example.com/x", ROOT, HOST_LIST, hosts) is True
    assert same_site("https://reports.example.com/x", ROOT, HOST_LIST, hosts) is True
    assert same_site("https://other.example.com/x", ROOT, HOST_LIST, hosts) is False


def test_list_always_admits_the_root_host():
    """A list cannot lock the crawl out of where it started."""
    assert same_site("https://app.example.com/x", ROOT, HOST_LIST,
                     ("admin.example.com",)) is True


def test_list_is_case_insensitive_and_ignores_blanks():
    assert same_site("https://ADMIN.example.com/x", ROOT, HOST_LIST,
                     ("  Admin.Example.com  ", "", "   ")) is True


def test_an_unknown_policy_narrows_rather_than_widens():
    """This is a scope gate. A gate that cannot read its configuration must
    fail closed — `config.py` is where a bad value is rejected loudly."""
    assert same_site("https://admin.example.com/x", ROOT, "anything-else") is False


# --- the config rejects a typo before anything opens ------------------------


def test_a_misspelled_policy_is_a_config_error():
    with pytest.raises(ValueError, match="scope.subdomains"):
        ScopeRules(subdomains="registrable_domain")   # underscore, not hyphen


def test_every_documented_policy_is_accepted():
    for policy in (SAME_HOST, REGISTRABLE_DOMAIN, HOST_LIST):
        assert ScopeRules(subdomains=policy).subdomains == policy


# --- through a real crawl ---------------------------------------------------


TWO_HOST_PAGES = {
    # Written at test time because the link must carry a real port, and a port
    # cannot be committed to a fixture.
    "index.html": """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Primary</title></head><body><main><h1>Primary host</h1>
<a href="{other}/second.html">The other host</a>
<a href="http://third-party.invalid/x.html">Off-site entirely</a>
</main></body></html>""",
    "second.html": """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Second</title></head><body><main><h1>Second host</h1></main></body></html>""",
}


@pytest.fixture
def two_hosts(serve, tmp_path):
    """One server, reached by two different hostnames.

    `127.0.0.1` and `localhost` are genuinely two hosts as far as every
    same-site rule is concerned, and both resolve without DNS — which is what
    makes this testable offline.
    """
    server = serve(str(tmp_path))
    other = f"http://localhost:{server.port}"
    (tmp_path / "index.html").write_text(
        TWO_HOST_PAGES["index.html"].format(other=other), encoding="utf-8")
    (tmp_path / "second.html").write_text(
        TWO_HOST_PAGES["second.html"], encoding="utf-8")
    return server, other


def _crawl(url: str, out, run=None, **kw):
    return asyncio.run(crawl_site(
        url, output_dir=str(out), run=run,
        options=CrawlOptions(max_pages=5, max_depth=2, screenshots=False,
                             probe=False, **kw),
    ))


def test_the_second_host_is_out_of_scope_by_default(two_hosts, tmp_path):
    server, _ = two_hosts
    crawl = _crawl(server.url("index.html"), tmp_path / "out")
    assert [n.url for n in crawl.pages] == [server.url("index.html")]


def test_the_second_host_is_captured_when_listed(two_hosts, tmp_path):
    server, _ = two_hosts
    crawl = _crawl(server.url("index.html"), tmp_path / "out",
                   subdomains=HOST_LIST, subdomain_hosts=("localhost",))
    captured = sorted(n.url for n in crawl.pages)
    assert len(captured) == 2, captured
    assert any("localhost" in u for u in captured)


@pytest.mark.parametrize(
    "policy,hosts",
    [(SAME_HOST, ()), (REGISTRABLE_DOMAIN, ()), (HOST_LIST, ("localhost",))],
)
def test_an_unrelated_host_is_never_reached_under_any_policy(
        two_hosts, tmp_path, policy, hosts):
    """Asserted on the request log rather than on the page count: a page the
    crawler declined to *capture* could still have been fetched, and "we never
    contacted them" is the claim worth proving. `G7`'s ledger is that log.
    """
    server, _ = two_hosts
    run = RunContext.begin(str(tmp_path / "run"), target=server.base)
    _crawl(server.url("index.html"), tmp_path / "out", run=run,
           subdomains=policy, subdomain_hosts=hosts)

    contacted = {h.host for h in run.manifest().egress.hosts}
    assert not any("third-party.invalid" in h for h in contacted), contacted
