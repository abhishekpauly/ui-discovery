"""Network request classification + secret redaction.

We record method / URL / resource-type / status / timing only. We never store
request or response *headers* or *bodies*, so auth headers, cookies and tokens
never enter the model. As defence in depth, query-string values for
sensitive-looking keys are redacted too.
"""

from __future__ import annotations

import re
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
