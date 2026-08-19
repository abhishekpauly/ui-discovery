"""Built-in adapters — small, generic, and useful on real portals.

These are also the worked examples for writing your own: each one is a few
lines and touches exactly one hook. Anything genuinely site-specific belongs
in your own module, not here.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import Page
from . import Adapter, register


@register
class ExtraWait(Adapter):
    """Wait a fixed extra interval before the page is read.

    The engine already waits for `networkidle` and then for the DOM to stop
    mutating, which covers most SPAs. This is the escape hatch for the ones it
    does not — a widget that paints on a timer, an animation that has to
    finish before a screenshot is worth taking.

        - name: extra_wait
          options: { ms: 1500 }
    """

    name = "extra_wait"

    async def post_navigate(self, page) -> None:
        await page.wait_for_timeout(int(self.options.get("ms", 1000)))


@register
class ExtraHeaders(Adapter):
    """Send extra HTTP headers with every request.

    Common on gated staging environments (`X-Preview-Token`, a CDN bypass
    header). Values live in your scope config, so treat that file as
    sensitive when you use this.

        - name: extra_headers
          options:
            headers: { X-Preview-Token: "abc123" }
    """

    name = "extra_headers"

    async def pre_navigate(self, context) -> None:
        headers = self.options.get("headers") or {}
        if not headers:
            return
        page = getattr(context, "page", None)
        if page is not None:
            await page.set_extra_http_headers(
                {str(k): str(v) for k, v in headers.items()}
            )


@register
class SkipPaths(Adapter):
    """Keep the crawler away from URLs matching regexes.

    `scope.exclude` in the config covers the ordinary case with globs; this is
    for the ones that need a real pattern (a query-string shape, a locale
    segment, an id range).

        - name: skip_paths
          options: { patterns: ["/reports/\\\\d{4}/", "[?&]export=1"] }
    """

    name = "skip_paths"

    def __init__(self, **options) -> None:
        super().__init__(**options)
        self._patterns = [re.compile(p) for p in options.get("patterns", [])]

    def should_visit(self, url: str) -> Optional[bool]:
        if any(p.search(url) for p in self._patterns):
            return False
        return None


@register
class LoggedInMarker(Adapter):
    """Product-specific "am I signed in?" check.

    The generic detector (auth.py) looks for password fields, login URLs and
    logged-out wording. When a product signals it differently — a user-menu
    that only renders when authenticated, a body class — say so here rather
    than widening the global heuristics for everyone.

        - name: logged_in_marker
          options:
            requires_control: "Account menu"    # accessible name that must exist
            forbids_control: "Continue with Google"
    """

    name = "logged_in_marker"

    def is_logged_in(self, page: Page) -> Optional[bool]:
        required = self.options.get("requires_control")
        forbidden = self.options.get("forbids_control")
        names = {
            (el.accessible_name or "").strip().lower()
            for el in page.elements if el.accessible_name
        }
        if forbidden and forbidden.strip().lower() in names:
            return False
        if required:
            return required.strip().lower() in names
        return None
