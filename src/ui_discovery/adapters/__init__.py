"""R3 — the adapter seam: site-specific behavior without editing the core.

Real portals have quirks the engine should not know about: a widget that
needs another second to settle, a staging environment behind a header, a
"logged in" state only that product can recognise. The principle is *config
and adapters over hacks* — so those quirks register here and are selected by
name in the scope config, and the core stays generic.

    adapters:
      - name: extra_wait
        options: { ms: 1500 }

An adapter implements only the hooks it cares about. Every hook has a
"no opinion" return (`None`), and hooks that express an opinion are combined
conservatively:

* `should_visit`  — any adapter saying False wins. An adapter can narrow the
  crawl, never widen it past the scope rules.
* `is_logged_in`  — any adapter saying False wins, because a false *negative*
  here means silently capturing login screens (see H4), which is the failure
  we care about.
* `on_page`       — every adapter runs; observation only.
* `pre_navigate`  — every adapter runs, in order.

Adapter failures never fail a crawl. A broken adapter is a bug in a plugin,
not a reason to lose the capture, so exceptions are caught, logged once and
the hook is treated as "no opinion".
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..models import Page

log = logging.getLogger(__name__)


class Adapter:
    """Base class. Subclass and override only the hooks you need.

    `name` is what a scope config selects; `options` is the config's
    `options:` mapping, validated by the adapter itself.
    """

    name: str = ""

    def __init__(self, **options: Any) -> None:
        self.options = options

    # --- pure hooks (no browser) -------------------------------------------

    def should_visit(self, url: str) -> Optional[bool]:
        """False to keep the crawler away from `url`. None = no opinion."""
        return None

    def is_logged_in(self, page: Page) -> Optional[bool]:
        """Product-specific "am I signed in?" check, overriding the generic
        heuristics in auth.py. None = no opinion."""
        return None

    def on_page(self, page: Page) -> None:
        """Observe a freshly extracted page. Runs after extraction, so it can
        read the model but must not need the browser."""

    # --- browser hooks (async crawler only) --------------------------------

    async def pre_navigate(self, context: Any) -> None:
        """Runs before each navigation, with Crawlee's pre-navigation context.
        Use for headers, cookies, viewport, init scripts."""

    async def post_navigate(self, page: Any) -> None:
        """Runs after navigation has settled but *before* the page is read.
        Use for waits the generic readiness checks cannot know about."""


# --- registry ----------------------------------------------------------------

_REGISTRY: dict[str, type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    """Register an adapter class under its `name`. Usable as a decorator."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} needs a `name` to be registered.")
    _REGISTRY[cls.name] = cls
    return cls


def available() -> list[str]:
    return sorted(_REGISTRY)


def build(specs: list[dict[str, Any]] | None) -> list[Adapter]:
    """Instantiate adapters from scope-config entries.

    An unknown adapter name is an error, not a warning: a config asking for
    behavior the engine cannot provide has not been honored, and pretending
    otherwise would produce a capture that silently ignored its own scope.
    """
    built: list[Adapter] = []
    for spec in specs or []:
        name = spec.get("name") if isinstance(spec, dict) else str(spec)
        if not name:
            raise ValueError("Adapter entry is missing a `name`.")
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(
                f"Unknown adapter {name!r}. Available: {', '.join(available()) or 'none'}"
            )
        options = (spec.get("options") or {}) if isinstance(spec, dict) else {}
        built.append(cls(**options))
    return built


# --- combining opinions ------------------------------------------------------

def _safe(adapter: Adapter, hook: str, *args):
    try:
        return getattr(adapter, hook)(*args)
    except Exception as exc:
        log.warning("adapter %s.%s failed: %s", adapter.name, hook, exc)
        return None


def should_visit(adapters: list[Adapter], url: str) -> bool:
    """Conservative: one veto is enough."""
    return all(_safe(a, "should_visit", url) is not False for a in adapters)


def is_logged_in(adapters: list[Adapter], page: Page) -> Optional[bool]:
    """Conservative: any adapter reporting "not signed in" wins, because
    missing that is how a crawl silently captures login screens."""
    verdicts = [_safe(a, "is_logged_in", page) for a in adapters]
    opinions = [v for v in verdicts if v is not None]
    if not opinions:
        return None
    return False if False in opinions else True


def on_page(adapters: list[Adapter], page: Page) -> None:
    for adapter in adapters:
        _safe(adapter, "on_page", page)


async def pre_navigate(adapters: list[Adapter], context: Any) -> None:
    for adapter in adapters:
        try:
            await adapter.pre_navigate(context)
        except Exception as exc:
            log.warning("adapter %s.pre_navigate failed: %s", adapter.name, exc)


async def post_navigate(adapters: list[Adapter], page: Any) -> None:
    for adapter in adapters:
        try:
            await adapter.post_navigate(page)
        except Exception as exc:
            log.warning("adapter %s.post_navigate failed: %s", adapter.name, exc)


# Importing the built-ins registers them.
from . import builtin  # noqa: E402,F401  (side-effect import, must come last)
