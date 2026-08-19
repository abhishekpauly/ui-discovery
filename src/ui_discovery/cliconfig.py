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
import sys
from typing import Optional

from .auth import DEFAULT_LOGGED_OUT_SIGNALS, DEFAULT_LOGIN_URL_PATTERNS
from .config import Scope, load_scope
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


def resolve_output_dir(scope: Scope, cli_output: Optional[str], slug: str) -> str:
    """`<dir>/<slug>`, or `<dir>/<YYYY-MM-DD>/<slug>` when the config asks to
    keep history — two snapshots are what `diff` needs, and re-running
    otherwise overwrites the previous one in place."""
    from datetime import date
    from pathlib import Path

    root = Path(pick(cli_output, scope.outputs.dir, "output"))
    if scope.outputs.keep_history:
        root = root / date.today().isoformat()
    return str(root / slug)


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
    if scope.safety.never_touch:
        lines.append(f"[INFO] never_touch: {scope.safety.never_touch}")
    return lines
