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
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .models import AuthCheck, Page

# --- H4: is this page telling us we are logged out? -------------------------
#
# Three independent signals, cheapest and most reliable first. Each is a fact
# about the rendered page, so the check stays deterministic and needs no
# knowledge of the product.

# URL path segments that conventionally mean "you are not signed in".
DEFAULT_LOGIN_URL_PATTERNS = (
    "login", "log-in", "signin", "sign-in", "sso",
    "auth", "oauth", "authenticate", "session/new",
)

# Phrases in a title or top heading that mean the same.
DEFAULT_LOGGED_OUT_SIGNALS = (
    "sign in", "sign-in", "signin", "log in", "log-in", "login",
    "session expired", "session has expired", "please authenticate",
    "continue with google", "continue with email", "single sign-on",
)


def _norm(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern:
    """Match any phrase on word boundaries.

    Substring matching is wrong here and quietly so: "De*signin*g reports"
    contains "signin", "Campaign" contains "paign". A false positive on this
    check reports a healthy crawl as an expired session, so it must not fire
    on words that merely spell a signal.
    """
    alternatives = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b")


def _url_segment_re(patterns: tuple[str, ...]) -> re.Pattern:
    """Match a pattern as a whole path segment, so `/login` fires but
    `/logingroup` does not."""
    alternatives = "|".join(re.escape(p) for p in sorted(patterns, key=len, reverse=True))
    return re.compile(rf"(?:^|/)(?:{alternatives})(?:/|$)")


def check_auth(
    page: Page,
    *,
    login_url_patterns: Optional[tuple[str, ...]] = None,
    logged_out_signals: Optional[tuple[str, ...]] = None,
) -> AuthCheck:
    """Decide whether `page` looks like a login / logged-out page.

    Pure and deterministic — it reads only the assembled model. Returns the
    signal that fired and the text that matched, so a report can show *why*
    rather than asserting it.
    """
    url_re = _url_segment_re(login_url_patterns or DEFAULT_LOGIN_URL_PATTERNS)
    phrase_re = _phrase_re(logged_out_signals or DEFAULT_LOGGED_OUT_SIGNALS)

    # 1. A visible password field is the least ambiguous signal there is:
    #    pages you are already authenticated into do not ask for a password.
    #    (Hidden ones are routine in signed-in "change password" flows.)
    for el in page.elements:
        if el.attributes.get("type", "").lower() == "password" and el.visible:
            return AuthCheck(
                looks_logged_out=True,
                signal="password-field",
                evidence=el.accessible_name or el.dom_path,
            )

    # 2. The URL we ended up on — after any redirect.
    path = _norm(urlparse(page.final_url or page.requested_url).path)
    if url_re.search(path):
        return AuthCheck(
            looks_logged_out=True, signal="login-url", evidence=page.final_url,
        )

    # 3. The page's own words: title first, then the top heading.
    haystacks = [("title", _norm(page.title))]
    if page.headings:
        haystacks.append(("heading", _norm(page.headings[0].text)))
    for where, text in haystacks:
        if text and phrase_re.search(text):
            return AuthCheck(
                looks_logged_out=True,
                signal=f"logged-out-{where}",
                evidence=text[:120],
            )

    # 4. Nothing rendered. A settled page with no headings and no interactive
    #    elements is not a page anyone shipped — it is an app that failed to
    #    start. Observed live: this portal's SPA renders a blank screen when
    #    its token is rejected, rather than redirecting to login, so without
    #    this the whole failure is invisible.
    settled = page.readiness.get("body_present") is not False
    if settled and not page.headings and not page.elements:
        return AuthCheck(
            looks_empty=True,
            signal="empty-page",
            evidence="page settled with no headings and no interactive elements",
        )

    return AuthCheck()


def load_storage_state(path: Optional[str]) -> Optional[dict[str, Any]]:
    """Load a saved storage-state file into a dict, or return None if no path."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Auth state file not found: {path}")
    try:
        # utf-8-sig, not utf-8: a session file re-saved by a Windows editor
        # (or PowerShell's `Set-Content -Encoding utf8`) carries a BOM, which
        # plain utf-8 hands to the JSON parser as a stray leading character.
        state = json.loads(p.read_text(encoding="utf-8-sig"))
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
