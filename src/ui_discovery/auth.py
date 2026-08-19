"""Session-based authentication via Playwright storage state.

We deliberately do NOT handle passwords, SSO, OTP or CAPTCHA in code. Instead:

  1. You log in once, by hand, in a real browser (`capture_session`, run
     locally with a visible browser).
  2. We save that browser's `storage_state` (cookies + localStorage) to a file.
  3. `extract` / `crawl` / `probe` load that file, so every page is fetched as
     the already-authenticated user.

This keeps credentials out of the tool entirely — the saved session is the only
secret, and it is created and kept by you. Treat the file like a password: it
grants access to your logged-in session until it expires.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def load_storage_state(path: Optional[str]) -> Optional[dict[str, Any]]:
    """Load a saved storage-state file into a dict, or return None if no path."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Auth state file not found: {path}")
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Auth state file is not valid JSON: {path} ({exc})")
    if not isinstance(state, dict) or "cookies" not in state:
        raise ValueError(
            f"Auth state file does not look like Playwright storage state: {path}"
        )
    return state


def capture_session(
    url: str,
    output: str,
    *,
    headless: bool = False,
    timeout_ms: int = 300000,
) -> str:
    """Open a browser at `url`, wait for the operator to log in, then save the
    session to `output`. Intended to be run **locally with a visible browser**
    (headless=False) so you can complete the login by hand.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, timeout=timeout_ms)
            print("\n" + "=" * 68)
            print("A browser window has opened. Log in to the portal there.")
            print("When you are fully logged in, come back here and press Enter")
            print("to save the session.")
            print("=" * 68)
            try:
                input("\nPress Enter once you are logged in... ")
            except EOFError:
                # Non-interactive fallback: give a fixed window to log in.
                page.wait_for_timeout(min(timeout_ms, 60000))
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=output)
            print(f"[INFO] Saved session to {output}")
            return output
        finally:
            browser.close()
