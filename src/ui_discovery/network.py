"""Network request classification + secret redaction.

We record method / URL / resource-type / status / timing only. We never store
request or response *headers* or *bodies*, so auth headers, cookies and tokens
never enter the model. As defence in depth, query-string values for
sensitive-looking keys are redacted too.
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_SENSITIVE_KEYS = re.compile(
    r"(token|api[_-]?key|apikey|secret|password|passwd|pwd|auth|"
    r"authorization|session|sig|signature|access[_-]?token|"
    r"refresh[_-]?token|code|bearer)",
    re.I,
)
_ID_SEG = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-fA-F-]{16,})$")


def redact_url(url: str, extra_keys: tuple[str, ...] = ()) -> str:
    """Redact values for sensitive-looking query keys. `extra_keys` adds
    target-specific ones from a scope config — additive only, so config can
    widen redaction but never narrow it."""
    try:
        parts = urlparse(url)
    except Exception:
        return url
    if not parts.query:
        return url

    def sensitive(key: str) -> bool:
        if _SENSITIVE_KEYS.search(key):
            return True
        lowered = key.lower()
        return any(extra.lower() in lowered for extra in extra_keys)

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted = [(k, "REDACTED" if sensitive(k) else v) for k, v in pairs]
    return urlunparse(parts._replace(query=urlencode(redacted)))


def describe_redaction(extra_keys: tuple[str, ...] = ()) -> dict:
    """G3: what network observation refuses to keep, as plain data.

    Lives here so the description cannot drift from the behaviour: the same
    module that drops the data says what it dropped. A manifest assembling this
    from its own idea of the rules would be a second source of truth, and the
    second one is always the one that goes stale.
    """
    return {
        "never_persisted": [
            "request headers (so bearer tokens and cookies never enter the model)",
            "response headers (so Set-Cookie never enters the model)",
            "request bodies",
            "response bodies",
        ],
        "redactions": [
            {
                "rule": "network.query_values",
                "applies_to": "recorded request URLs",
                "detail": (
                    "values of query keys matching token / api_key / secret / "
                    "password / auth / session / signature / code / bearer are "
                    "replaced with REDACTED"),
            },
        ],
        "network_keys_extra": sorted(k.lower() for k in extra_keys),
    }


def host_of(url: str) -> str:
    """The `host:port` a URL addresses, lowercased. Empty when unparseable."""
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def build_ledger(urls: Iterable[str], target: str) -> dict:
    """G7: roll observed request URLs up into a per-host ledger.

    A rollup, not new instrumentation: `F3.4` already records every request the
    browser made, and this counts them by host. Pure, so it can be tested
    without a browser and reused by anything that has a list of URLs.

    Scope is decided by exact host match, which is what `same-host` — the
    engine's only subdomain policy today — actually means. `H6` is the item
    that widens it; until then a ledger claiming `cdn.target.com` is in scope
    would be claiming something the crawler does not believe.

    URLs arriving here have already been through `redact_url`, so nothing this
    returns can carry a secret that the recorded requests did not already.
    """
    target_host = host_of(target)
    seen: dict[str, dict] = {}
    total = 0
    for url in urls:
        host = host_of(url)
        if not host:
            continue
        total += 1
        entry = seen.get(host)
        if entry is None:
            try:
                path = urlparse(url).path or "/"
            except Exception:
                path = "/"
            seen[host] = {"host": host, "requests": 1, "first_path": path,
                          "in_scope": host == target_host}
        else:
            entry["requests"] += 1
    hosts = sorted(seen.values(), key=lambda h: (not h["in_scope"], h["host"]))
    return {
        "target_host": target_host,
        "hosts": hosts,
        "total_requests": total,
        "off_scope": [h["host"] for h in hosts if not h["in_scope"]],
    }


def endpoint_pattern(url: str) -> str:
    """Normalize identifier-looking path segments to `:id` so distinct records
    collapse onto a single endpoint template."""
    try:
        parts = urlparse(url)
    except Exception:
        return url
    segs = [
        ":id" if _ID_SEG.match(seg) else seg
        for seg in parts.path.split("/")
    ]
    return (parts.netloc + "/".join(segs)) or url


def classify(method: str, url: str, resource_type: str) -> tuple[bool, bool, str]:
    """Return (is_api, is_graphql, endpoint_pattern)."""
    is_api = resource_type in ("xhr", "fetch")
    path = urlparse(url).path.lower()
    is_graphql = "graphql" in path or (method.upper() == "POST" and path.endswith("/gql"))
    return is_api, is_graphql, endpoint_pattern(url)
