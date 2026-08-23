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

from pydantic import BaseModel, Field, field_validator, model_validator


class ScopeRules(BaseModel):
    """Which URLs are in scope, beyond the same-domain restriction."""

    # Glob-style patterns matched against the URL path (see util.path_matches).
    include: list[str] = Field(default_factory=list)  # empty = everything
    exclude: list[str] = Field(default_factory=list)

    # H6: how much of a hostname counts as the same site. Default is today's
    # behaviour, so nothing moves for a config that does not mention it.
    #   same-host           — exact netloc, port included
    #   registrable-domain  — subdomains unify (app./admin. of one domain)
    #   list                — the explicit hosts below, plus the start URL's own
    subdomains: str = "same-host"
    subdomain_hosts: list[str] = Field(default_factory=list)

    @field_validator("subdomains")
    @classmethod
    def _known_policy(cls, value: str) -> str:
        from .util import SUBDOMAIN_POLICIES

        if value not in SUBDOMAIN_POLICIES:
            raise ValueError(
                f"scope.subdomains: {value!r} is not one of "
                f"{', '.join(SUBDOMAIN_POLICIES)}.")
        return value


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
    """Feature switches."""

    screenshots: bool = True
    accessibility_tree: bool = True
    # The master switch for interaction. On by default: a capture that never
    # clicks anything cannot see a modal, a menu, a tab panel or an API call,
    # which is most of what a portal is. Turn it off with `--no-probe`, or
    # scope it down per module with the `probe:` block below — that is usually
    # the better answer on a large portal.
    probe: bool = True
    network: bool = True  # only meaningful with probe
    # Click elements the app never marked up as links, to find routes
    # nothing else can reach. See CrawlOptions.deep_nav.
    deep_nav: bool = True


class Discovery(BaseModel):
    """M1 — where the crawl gets its URLs from, besides following links."""

    # include — read robots.txt / sitemap.xml and seed from them (default)
    # skip    — reproduce the crawl exactly as it was before M1 existed
    # only    — crawl the sitemap and follow nothing; the fast survey
    sitemap: str = "include"
    # Seconds to wait on each sitemap request. A product that does not answer
    # promptly is not worth stalling a capture over.
    sitemap_timeout: float = 10.0

    @field_validator("sitemap")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        from .discovery import SITEMAP_MODES

        if value not in SITEMAP_MODES:
            raise ValueError(
                f"discovery.sitemap: {value!r} is not one of "
                f"{', '.join(SITEMAP_MODES)}.")
        return value


class Capture(BaseModel):
    """H9 — what the engine models, as opposed to what it interacts with.

    Deliberately separate from `Safety`: `safety.never_touch` forbids
    *interacting* with something the model still describes, which is the right
    answer for a Delete button. This forbids *modelling* at all, which is the
    right answer for a vendor's chat widget — it is not part of your product
    and does not belong in a document about it.
    """

    # CSS selectors whose subtrees are excluded from extraction entirely.
    # A selector matching a landmark is refused rather than honoured: `main`
    # and `nav` are the page's own structure, and a selector broad enough to
    # catch one is a mistake rather than an instruction.
    exclude_selectors: list[str] = Field(default_factory=list)


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

    # G5: redact people out of *displayed* page content — element text,
    # accessible names, options, headings, titles and the ARIA snapshot.
    #
    # Off by default, which is a deliberate choice rather than an oversight.
    # Redaction costs recall on the capture's own content, so it is a decision
    # an operator makes for a target they know. `G3`'s manifest posture records
    # which way it was set, so a capture always says which it was.
    redact_content: bool = False
    # Which entity kinds to look for. Empty means the default set (everything
    # except PERSON, which needs names to match against).
    redact_entities: list[str] = Field(default_factory=list)
    # tag (`<EMAIL>`) | mask (`****`) | remove
    redact_style: str = "tag"
    # Names the operator supplied. A pattern cannot find a person's name, so
    # this is the seam where knowledge the engine cannot have gets in.
    person_names: list[str] = Field(default_factory=list)

    # G6: cover the same people in the pictures. Unset follows
    # `redact_content`, because a capture whose model is clean and whose
    # screenshots are not protects nobody, and two independent switches is how
    # that happens by accident. Set it explicitly to break the pairing.
    redact_screenshots: Optional[bool] = None

    def mask_screenshots(self) -> bool:
        """Whether screenshots are masked, after the default is resolved."""
        if self.redact_screenshots is None:
            return bool(self.redact_content)
        return bool(self.redact_screenshots)


# Captures are deliverables, not build output: you open them, attach them to
# a ticket, hand them to someone. Downloads is where a person looks for a file
# they just generated, and it keeps 9MB of screenshots out of the repo.
DOWNLOADS = str(Path.home() / "Downloads")


# X9 — named presets over the toggles that already exist.
#
# `Capabilities` has five switches, `ProbeSettings` several more and `Budget`
# a few numbers. That is the right amount of *control* and the wrong amount of
# *decision*: an operator wants to express an intent — "have a look round",
# "the full documentation pass" — not nine booleans, and the honest default in
# the absence of that is to run everything and wait.
#
# Each preset is a plain mapping over fields that already exist. Nothing here
# can reach a setting the config could not set by hand, and `standard` is
# exactly today's defaults, so a config that ignores this is unaffected.
CAPTURE_PROFILES: dict[str, dict[str, dict]] = {
    # Reconnaissance: what screens exist and what is on them. No clicking, so
    # no modals, menus, tab panels or API traffic — the cost of being fast,
    # stated rather than buried.
    "fast": {
        "capabilities": {"screenshots": False, "accessibility_tree": False,
                         "probe": False, "deep_nav": False},
        "probe": {"state_capture": False, "component_screenshots": False},
    },
    # Today's behaviour, named so it can be chosen deliberately.
    "standard": {},
    # The full documentation pass: everything on, and a bigger interaction
    # budget because the point is coverage rather than wall-clock.
    "deep": {
        "capabilities": {"screenshots": True, "accessibility_tree": True,
                         "probe": True, "deep_nav": True},
        "probe": {"state_capture": True, "component_screenshots": True},
        "budget": {"max_interactions": 80},
    },
}


class Outputs(BaseModel):
    # Empty means "the Downloads folder" — resolved at use, not import, so a
    # config written on one machine still works on another.
    dir: str = ""
    # One folder per run (dated) instead of overwriting in place, so two
    # snapshots exist to compare with `diff` (C1).
    keep_history: bool = False
    run_label: Optional[str] = None
    # G4: how long a capture may live before `prune` will offer to remove it.
    # Zero means retention is off, which is the default — a capture is somebody's
    # deliverable, and an engine that started deleting them because a config
    # gained a key would be worse than one that never deletes at all.
    retention_days: int = 0
    # X9: a named preset over the capability toggles. `standard` is exactly
    # today's defaults. Explicit keys always win — see `Scope._apply_profile`.
    profile: str = "standard"

    @field_validator("profile")
    @classmethod
    def _known_profile(cls, value: str) -> str:
        if value not in CAPTURE_PROFILES:
            raise ValueError(
                f"outputs.profile: {value!r} is not one of "
                f"{', '.join(CAPTURE_PROFILES)}.")
        return value


class AdapterSpec(BaseModel):
    """Selects a registered adapter by name (R3). An unknown name is an error
    at load time — a config asking for behavior the engine cannot provide has
    not been honored."""

    name: str
    options: dict[str, Any] = Field(default_factory=dict)


class ProbeSettings(BaseModel):
    """How thoroughly to interact with one area of a product.

    Every field is optional, and unset means *inherit* — from the module's
    parent settings, then from the top-level `probe:` block, then from
    `capabilities.probe` / `budget.max_interactions`. That is what lets a
    config say "probe everything at the defaults, except Reports, which is
    read-only, and Orders, which needs a bigger budget" without restating the
    defaults three times.

    The tab policy can only ever *narrow* what gets clicked. There is
    deliberately no setting that widens the safety allow-list, for the same
    reason `Safety` has no `block_words_remove`: a config file is the wrong
    place to be able to talk the engine into clicking something.
    """

    enabled: Optional[bool] = None
    max_interactions: Optional[int] = None
    # Photograph the modal / drawer / menu / tab panel each click reveals.
    state_capture: Optional[bool] = None
    # Cropped pictures of the components already on a settled page.
    component_screenshots: Optional[bool] = None
    # CSS for components standard markup cannot name — a card, a tile, a
    # dashboard widget. `taxonomy.NOT_DETECTABLE` records why the engine will
    # not guess at these; naming them here is how you get them captured.
    component_selectors: list[str] = Field(default_factory=list)

    # all    — open every tab (the default)
    # none   — open no tabs; they are recorded but never clicked
    # listed — open only the tabs named in `tab_labels`
    tabs: Optional[str] = None
    tab_labels: list[str] = Field(default_factory=list)
    # Never opened, in any mode. Wins over `tab_labels`.
    tab_exclude: list[str] = Field(default_factory=list)

    @field_validator("tabs")
    @classmethod
    def _known_tab_policy(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"all", "none", "listed"}
        if v not in allowed:
            raise ValueError(
                f"tabs: {v!r} is not a tab policy. Use one of "
                f"{', '.join(sorted(allowed))}."
            )
        return v


class Module(BaseModel):
    """A separately-crawlable area of the product."""

    name: str
    start_url: str
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None
    # Per-module probe settings. Pages are matched to a module by longest
    # URL-path prefix (`util.module_for_path`), the same rule that decides
    # which module folder a page's artifacts are written to — so a page is
    # never probed with one module's settings and filed under another's.
    probe: Optional[ProbeSettings] = None


# G1: environments the authorization gate treats as production. A short,
# closed list rather than a pattern — "preprod" and "prod-mirror" are not
# production, and guessing at them would refuse runs nobody asked to refuse.
PROD_ENVIRONMENTS = {"prod", "production"}


class Scope(BaseModel):
    """The whole scope config — the machine-readable form of the operator
    intake questionnaire."""

    target: str = ""
    start_url: Optional[str] = None
    modules: list[Module] = Field(default_factory=list)
    # Informational only: the engine observes traffic, it never calls these.
    known_endpoints: list[str] = Field(default_factory=list)

    # G1: authorization is recorded *and* enforced. The engine cannot verify
    # that a person really approved this — no software can — so it does the one
    # honest thing available: against production, it refuses to start until
    # someone has put their name to it. See `authorization_refusal`.
    authorized: Optional[bool] = None
    authorized_by: Optional[str] = None
    environment: Optional[str] = None  # prod | staging | sandbox

    scope: ScopeRules = Field(default_factory=ScopeRules)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    budget: Budget = Field(default_factory=Budget)
    identity: Identity = Field(default_factory=Identity)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    capture: Capture = Field(default_factory=Capture)
    discovery: Discovery = Field(default_factory=Discovery)
    # Defaults for every module. A module's own `probe:` overrides these
    # field by field; anything unset here falls back to `capabilities.probe`
    # and `budget.max_interactions`, so existing configs keep working.
    probe: ProbeSettings = Field(default_factory=ProbeSettings)
    politeness: Politeness = Field(default_factory=Politeness)
    safety: Safety = Field(default_factory=Safety)
    privacy: Privacy = Field(default_factory=Privacy)
    outputs: Outputs = Field(default_factory=Outputs)
    adapters: list[AdapterSpec] = Field(default_factory=list)

    model_config = {"extra": "forbid"}  # a typo'd key is an error, not a no-op

    @model_validator(mode="after")
    def _apply_profile(self) -> "Scope":
        """X9: fold the named preset into the toggles, without ever
        overriding something the config stated.

        `model_fields_set` is what makes "explicit keys always win" real
        rather than aspirational: it distinguishes a value that happens to
        equal the default from one the operator actually wrote. A preset that
        silently reverted a hand-set `screenshots: true` would be a config
        file arguing with its author.

        Applied here rather than at each CLI, so the *resolved* scope is what
        gets hashed into `run.json`'s `config_sha256` — two runs that differ
        only in how they spelled the same settings are provably the same
        configuration, and the manifest records what was done rather than the
        name of a preset a reader would have to look up.
        """
        preset = CAPTURE_PROFILES.get(self.outputs.profile) or {}
        for section, values in preset.items():
            target = getattr(self, section)
            for key, value in values.items():
                if key in target.model_fields_set:
                    continue  # the operator said so; the preset does not argue
                setattr(target, key, value)
        return self

    def resolved_capture(self) -> dict:
        """The capability set this config actually runs with, for the record.

        `X9` exists so an operator can say `fast` instead of nine booleans;
        this is the other half of that bargain — the capture still states
        precisely what it did, so nobody has to know what `fast` meant in the
        version that ran.
        """
        return {
            "profile": self.outputs.profile,
            "screenshots": self.capabilities.screenshots,
            "accessibility_tree": self.capabilities.accessibility_tree,
            "probe": self.capabilities.probe,
            "deep_nav": self.capabilities.deep_nav,
            "network": self.capabilities.network,
            "state_capture": self.probe.state_capture,
            "component_screenshots": self.probe.component_screenshots,
            "max_pages": self.budget.max_pages,
            "max_depth": self.budget.max_depth,
            "max_interactions": self.budget.max_interactions,
        }

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

    # --- authorization (G1) --------------------------------------------------

    def authorization_refusal(self) -> Optional[str]:
        """Why this run must not start, or `None` if it may.

        The engine cannot verify that anyone approved a capture — no software
        can. What it can do is refuse to point at production on nobody's
        authority. Against a `prod` environment, `authorized: true` and a
        non-empty `authorized_by` are required; everywhere else these fields
        stay what they always were, a note on the record.

        Deliberately narrow. A gate that fired on staging would be turned off
        within a week, and a gate that is off protects nothing.

        Pure and importable: the CLIs decide what to *do* about a refusal, but
        the rule itself is a library function, testable without a process.
        """
        if (self.environment or "").strip().lower() not in PROD_ENVIRONMENTS:
            return None
        missing = []
        if self.authorized is not True:
            missing.append("`authorized: true`")
        if not (self.authorized_by or "").strip():
            missing.append("`authorized_by:` naming who approved it")
        if not missing:
            return None
        return (
            f"This config sets `environment: {self.environment}` but is "
            f"missing {' and '.join(missing)}. A production capture opens "
            f"real screens, clicks real controls and photographs whatever is "
            f"on them, so it does not start on nobody's authority. Add the "
            f"field(s) above, or set an `environment:` that is not production."
        )


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


def load_scope(path: Optional[str], profile: Optional[str] = None) -> Scope:
    """Load a scope config, or return all-defaults when no path is given.

    `profile` is `X9`'s CLI override, and it is injected into the *raw* data
    before validation rather than set on the finished model. That matters:
    the preset only fills fields the config did not state, and it reads
    "did not state" from `model_fields_set`. Round-tripping a built model
    through `model_dump()` marks every field as set, which would silently
    turn the preset into a no-op.
    """
    if not path:
        return Scope.model_validate(
            {"outputs": {"profile": profile}} if profile else {})
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
    if profile:
        outputs = dict(data.get("outputs") or {})
        outputs["profile"] = profile
        data = {**data, "outputs": outputs}
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
