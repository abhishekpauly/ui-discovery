"""Shared CLI plumbing for scope configs.

One module owns the whole flags-over-config-over-defaults story, so every
command resolves settings the same way and precedence cannot drift between
them.

The rule: **an explicitly-typed flag always wins.** That is why the commands
declare their config-backed flags with `default=None` rather than a real
default — it is the only way to tell "the user asked for 25 pages" apart from
"argparse filled in 25", and silently overriding a config file with a default
nobody typed is exactly the bug this avoids.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from .adapters import build as build_adapters
from .auth import DEFAULT_LOGGED_OUT_SIGNALS, DEFAULT_LOGIN_URL_PATTERNS
from .config import ProbeSettings, Scope, load_scope
from .crawler import CrawlOptions
from .interactions import ProbeProfile
from .safety import SafetyPolicy


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", default=None, metavar="FILE",
        help="Scope config (.yaml or .json) describing target, scope, "
             "budgets, capabilities and safety. Flags override it. "
             "See: python -m ui_discovery.intake",
    )


def pick(flag_value, config_value, default):
    """Flags > config > default. `None` means 'not specified'."""
    if flag_value is not None:
        return flag_value
    if config_value is not None:
        return config_value
    return default


def load_or_exit(path: Optional[str]) -> Scope:
    """Load a scope config, reporting a clean error rather than a traceback."""
    try:
        return load_scope(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)


def adapters_for(scope: Scope):
    """Instantiate the config's adapters, reporting an unknown name cleanly
    rather than as a traceback."""
    try:
        return build_adapters([a.model_dump() for a in scope.adapters])
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)


def safety_policy(scope: Scope) -> SafetyPolicy:
    return SafetyPolicy(
        block_words_extra=frozenset(scope.safety.block_words_extra),
        caution_words_extra=frozenset(scope.safety.caution_words_extra),
        never_touch=tuple(scope.safety.never_touch),
    )


def auth_signals(scope: Scope) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Config *extends* the built-in expiry signals; it never replaces them,
    so a config file cannot accidentally blind the expiry check."""
    return (
        DEFAULT_LOGIN_URL_PATTERNS + tuple(scope.auth.login_url_patterns),
        DEFAULT_LOGGED_OUT_SIGNALS + tuple(scope.auth.logged_out_signals),
    )


def probe_profile(
    scope: Scope, args, settings: Optional[ProbeSettings] = None,
) -> ProbeProfile:
    """One fully-resolved `ProbeProfile`, following the engine's usual
    precedence: **flags > module > top-level `probe:` > capabilities/budget**.

    `settings` is a module's own block, or None for the crawl-wide default.
    Every field falls back through the layers, so a config only ever has to
    state what differs from the level above it.

    Flags are deliberately coarse and win everywhere: `--no-probe` means no
    probe, whatever any module says. Per-module and per-tab detail is
    config-only, which is right — it is knowledge about one target, and that
    is what `intake.py` exists to capture.
    """
    base = scope.probe
    mod = settings or ProbeSettings()

    def layered(field, fallback):
        for source in (mod, base):
            value = getattr(source, field)
            if value is not None:
                return value
        return fallback

    def listed(field) -> tuple[str, ...]:
        # A module that names its own list replaces the default; one that names
        # nothing inherits it. Merging the two would make it impossible to
        # narrow a list, which is the whole point of naming one.
        return tuple(getattr(mod, field) or getattr(base, field) or ())

    enabled = layered("enabled", scope.capabilities.probe)
    if getattr(args, "no_probe", None):
        enabled = False
    elif getattr(args, "probe", None):
        enabled = True

    state_capture = layered("state_capture", True)
    if getattr(args, "no_state_capture", None):
        state_capture = False
    component_screenshots = layered("component_screenshots", True)
    if getattr(args, "no_component_screenshots", None):
        component_screenshots = False

    max_interactions = pick(
        getattr(args, "max_interactions", None),
        layered("max_interactions", None),
        scope.budget.max_interactions,
    )

    return ProbeProfile(
        enabled=bool(enabled),
        max_interactions=int(max_interactions),
        state_capture=bool(state_capture),
        component_screenshots=bool(component_screenshots),
        component_selectors=listed("component_selectors"),
        tabs=layered("tabs", "all"),
        tab_labels=listed("tab_labels"),
        tab_exclude=listed("tab_exclude"),
    )


def probe_rules(scope: Scope, args) -> tuple[tuple[str, ProbeProfile], ...]:
    """(url-path prefix, profile) for every module that configures the probe.

    Resolved here, once, rather than in the crawler: the crawler should not
    have to know what a `Scope` is.
    """
    from urllib.parse import urlparse

    rules = []
    for module in scope.modules:
        path = (urlparse(module.start_url).path or "/").rstrip("/")
        if not path:
            continue
        rules.append((path, probe_profile(scope, args, module.probe)))
    return tuple(rules)


def crawl_options(scope: Scope, args) -> CrawlOptions:  # noqa: ANN001
    """Resolve every crawl setting from flags + config, in one place.

    `crawl` and `pipeline` both need this. Duplicating it would let the two
    commands drift — the same config quietly producing different crawls
    depending on which entry point you used.

    Expects the argparse namespace to carry the config-backed flags declared
    by `add_crawl_arguments`; anything absent falls back to the config, then
    to the default.
    """
    def flag(name, transform=lambda v: v):
        value = getattr(args, name, None)
        return None if value is None else transform(value)

    drop_params = getattr(args, "drop_param", None) or scope.identity.drop_params
    login_patterns, logged_out = auth_signals(scope)
    default_profile = probe_profile(scope, args)

    return CrawlOptions(
        max_pages=pick(flag("max_pages"), scope.budget.max_pages, 25),
        max_depth=pick(flag("max_depth"), scope.budget.max_depth, 3),
        # Headed by default from the CLIs: someone running this by hand
        # should see what it is doing. The library default stays headless,
        # since programmatic callers and tests do not want windows.
        headless=bool(getattr(args, "headless", False)),
        dedupe_queries=pick(flag("dedupe_queries"),
                            scope.identity.dedupe_queries, False),
        drop_params=frozenset(drop_params) or None,
        hash_routes=pick(flag("hash_routes"), scope.identity.hash_routes, False),
        probe=default_profile.enabled,
        max_interactions=default_profile.max_interactions,
        probe_default=default_profile,
        probe_rules=probe_rules(scope, args),
        component_screenshots=default_profile.component_screenshots,
        component_selectors=default_profile.component_selectors,
        state_capture=default_profile.state_capture,
        include=scope.scope.include,
        exclude=scope.scope.exclude,
        screenshots=pick(
            False if getattr(args, "no_screenshots", None) else None,
            scope.capabilities.screenshots, True),
        accessibility_tree=scope.capabilities.accessibility_tree,
        login_url_patterns=login_patterns,
        logged_out_signals=logged_out,
        policy=safety_policy(scope),
        redact_keys=tuple(scope.privacy.redact_network_keys),
        adapters=tuple(adapters_for(scope)),
        max_requests_per_minute=pick(
            flag("max_requests_per_minute"),
            scope.politeness.max_requests_per_minute, None),
        max_concurrency=pick(flag("max_concurrency"),
                             scope.politeness.max_concurrency, 100),
        respect_robots_txt=pick(flag("respect_robots_txt"),
                                scope.politeness.respect_robots_txt, False),
        # `modules:` finally does something: each one is an extra start
        # URL. This is the answer for routes no amount of link-following
        # reaches — a contextual sidebar, or a nav item that is a click
        # handler rather than an anchor.
        seeds=tuple(m.start_url for m in scope.modules if m.start_url)
              + tuple(getattr(args, "seed", None) or ()),
        reveal_nav=not getattr(args, "no_reveal_nav", False),
        deep_nav=pick(
            False if getattr(args, "no_deep_nav", None) else None,
            scope.capabilities.deep_nav, True),
    )


def run_folder_name(scope: Scope, slug: str) -> str:
    """The per-run folder name: the **product name** when the config gives one
    (`outputs.run_label`, else `target`), otherwise the URL slug.

    A folder called `acme-portal` is findable months later; one called
    `portal.example.com_platform_dashboard` is not.
    """
    label = scope.outputs.run_label or scope.target
    if not label:
        return slug
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip()).strip("-._")
    return cleaned[:80] or slug


def resolve_output_dir(scope: Scope, cli_output: Optional[str], slug: str) -> str:
    """`<dir>/<product>`, or `<dir>/<YYYY-MM-DD>/<product>` when the config
    asks to keep history — two snapshots are what `diff` needs, and re-running
    otherwise overwrites the previous one in place."""
    from datetime import date
    from pathlib import Path

    from .config import DOWNLOADS

    root = Path(pick(cli_output, scope.outputs.dir or None, DOWNLOADS))
    if scope.outputs.keep_history:
        root = root / date.today().isoformat()
    return str(root / run_folder_name(scope, slug))


def describe(scope: Scope, config_path: Optional[str]) -> list[str]:
    """Lines summarising an active config, for the run log. Silent when no
    config was supplied, so zero-config runs stay quiet."""
    if not config_path:
        return []
    lines = [f"[INFO] Config: {config_path}"
             + (f" (target: {scope.target})" if scope.target else "")]
    if scope.authorized is False:
        lines.append("[WARN] Config records authorized: false — check you are "
                     "permitted to run against this target.")
    if scope.environment and scope.environment.lower() in ("prod", "production"):
        lines.append("[WARN] Config records environment: prod. Prefer a "
                     "non-production target where possible.")
    if scope.scope.include or scope.scope.exclude:
        lines.append(f"[INFO] Scope: include={scope.scope.include or ['*']} "
                     f"exclude={scope.scope.exclude or []}")
    if scope.adapters:
        lines.append("[INFO] Adapters: "
                     + ", ".join(a.name for a in scope.adapters))
    if (scope.politeness.max_requests_per_minute
            or scope.politeness.respect_robots_txt):
        lines.append(
            f"[INFO] Politeness: "
            f"{scope.politeness.max_requests_per_minute or 'unlimited'} req/min"
            + (", robots.txt respected"
               if scope.politeness.respect_robots_txt else ""))
    if scope.safety.never_touch:
        lines.append(f"[INFO] never_touch: {scope.safety.never_touch}")
    return lines
