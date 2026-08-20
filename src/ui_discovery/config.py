"""Scope configuration — per-target behavior as data, not flags or code.

    python -m ui_discovery.crawl <url> --config scope.yaml

One file describes what to crawl, as whom, how much, what to capture and what
never to touch. It doubles as the audit record of what was in scope and why,
which is the point of the operator intake questionnaire that generates it
(`python -m ui_discovery.intake`).

Three rules govern this module:

* **Zero-config still works.** Every default reproduces today's behavior, so a
  bare `crawl <url>` is unchanged.
* **Precedence is flags > config > defaults.** An explicit flag always wins;
  see `Scope.merge_cli`.
* **If it is in this schema, it is wired.** A toggle that silently did nothing
  would be worse than no toggle at all, so nothing is declared here that the
  engine does not actually honor.

YAML and JSON are both accepted, chosen by file extension.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class ScopeRules(BaseModel):
    """Which URLs are in scope, beyond the same-domain restriction."""

    # Glob-style patterns matched against the URL path (see util.path_matches).
    include: list[str] = Field(default_factory=list)  # empty = everything
    exclude: list[str] = Field(default_factory=list)


class AuthSettings(BaseModel):
    required: bool = False
    state_file: Optional[str] = None
    # Extend (never replace) the built-in expiry signals in auth.py.
    login_url_patterns: list[str] = Field(default_factory=list)
    logged_out_signals: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    max_pages: int = 25
    max_depth: int = 3
    max_interactions: int = 40


class Identity(BaseModel):
    """What counts as "the same page" (H1)."""

    dedupe_queries: bool = False
    hash_routes: bool = False
    drop_params: list[str] = Field(default_factory=list)


class Politeness(BaseModel):
    """X5 — how hard to push a target.

    Defaults are the engine's existing behavior, so nothing changes unless
    you ask. But when the target is shared infrastructure (a team's staging
    portal), a rate cap is the difference between a capture and an incident.
    """

    # Requests per minute across the whole crawl. None = unlimited.
    max_requests_per_minute: Optional[float] = None
    # Upper bound on parallel pages. Crawlee autoscales below this.
    max_concurrency: int = 100
    # Honour the target's robots.txt. Off by default because the engine is
    # pointed at products you own and are authorized to test, where robots
    # rules are written for search engines, not for you.
    respect_robots_txt: bool = False


class Capabilities(BaseModel):
    """Feature switches. Defaults reproduce today's behavior exactly."""

    screenshots: bool = True
    accessibility_tree: bool = True
    probe: bool = False
    network: bool = True  # only meaningful with probe
    # Click elements the app never marked up as links, to find routes
    # nothing else can reach. See CrawlOptions.deep_nav.
    deep_nav: bool = True


class Safety(BaseModel):
    """Additions to the interaction safety envelope. Deliberately additive:
    config can make the engine *more* cautious, never less. There is no
    `block_words_remove`, and `submit_forms` cannot be turned on."""

    block_words_extra: list[str] = Field(default_factory=list)
    caution_words_extra: list[str] = Field(default_factory=list)
    # Accessible names / dom_path fragments that must never be interacted with.
    never_touch: list[str] = Field(default_factory=list)
    submit_forms: bool = False

    @field_validator("submit_forms")
    @classmethod
    def _forms_stay_unsubmitted(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "submit_forms: true is not supported — the engine fills forms "
                "but never submits them. Remove this key."
            )
        return v


class Privacy(BaseModel):
    # Extra query-string keys whose values are redacted in recorded URLs.
    redact_network_keys: list[str] = Field(default_factory=list)


# Captures are deliverables, not build output: you open them, attach them to
# a ticket, hand them to someone. Downloads is where a person looks for a file
# they just generated, and it keeps 9MB of screenshots out of the repo.
DOWNLOADS = str(Path.home() / "Downloads")


class Outputs(BaseModel):
    # Empty means "the Downloads folder" — resolved at use, not import, so a
    # config written on one machine still works on another.
    dir: str = ""
    # One folder per run (dated) instead of overwriting in place, so two
    # snapshots exist to compare with `diff` (C1).
    keep_history: bool = False
    run_label: Optional[str] = None


class AdapterSpec(BaseModel):
    """Selects a registered adapter by name (R3). An unknown name is an error
    at load time — a config asking for behavior the engine cannot provide has
    not been honored."""

    name: str
    options: dict[str, Any] = Field(default_factory=dict)


class Module(BaseModel):
    """A separately-crawlable area of the product."""

    name: str
    start_url: str
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None


class Scope(BaseModel):
    """The whole scope config — the machine-readable form of the operator
    intake questionnaire."""

    target: str = ""
    start_url: Optional[str] = None
    modules: list[Module] = Field(default_factory=list)
    # Informational only: the engine observes traffic, it never calls these.
    known_endpoints: list[str] = Field(default_factory=list)

    # Authorization is recorded, not enforced — the engine cannot verify it.
    # It exists so a capture carries the answer to "who approved this?".
    authorized: Optional[bool] = None
    authorized_by: Optional[str] = None
    environment: Optional[str] = None  # prod | staging | sandbox

    scope: ScopeRules = Field(default_factory=ScopeRules)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    budget: Budget = Field(default_factory=Budget)
    identity: Identity = Field(default_factory=Identity)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    politeness: Politeness = Field(default_factory=Politeness)
    safety: Safety = Field(default_factory=Safety)
    privacy: Privacy = Field(default_factory=Privacy)
    outputs: Outputs = Field(default_factory=Outputs)
    adapters: list[AdapterSpec] = Field(default_factory=list)

    model_config = {"extra": "forbid"}  # a typo'd key is an error, not a no-op

    # --- resolution ---------------------------------------------------------

    def resolve_start_url(self, cli_url: Optional[str]) -> str:
        """The URL to crawl: an explicit argument wins, else the config's."""
        url = cli_url or self.start_url
        if not url:
            raise ValueError(
                "No start URL: pass one on the command line or set "
                "`start_url:` in the config."
            )
        if self.is_excluded(url):
            raise ValueError(
                f"Start URL {url} matches an `exclude` pattern in this scope "
                f"config — refusing to crawl out of scope."
            )
        return url

    def is_excluded(self, url: str) -> bool:
        from .util import url_in_scope

        return not url_in_scope(url, self.scope.include, self.scope.exclude)


def _read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ValueError(
                f"Reading {path.name} needs PyYAML (`pip install pyyaml`), or "
                f"use a .json config instead."
            ) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text or "{}")


def load_scope(path: Optional[str]) -> Scope:
    """Load a scope config, or return all-defaults when no path is given."""
    if not path:
        return Scope()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    try:
        data = _read(p)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Config file is not valid {p.suffix or 'JSON'}: "
                         f"{path} ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a mapping at the top "
                         f"level: {path}")
    return Scope.model_validate(data)


def dump_scope(scope: Scope, path: str) -> str:
    """Write a scope config back out, in the format its extension implies."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = scope.model_dump(exclude_none=True)
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml

        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    p.write_text(text, encoding="utf-8")
    return str(p)
