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

import base64
import json
import re
import time
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


# --- session pre-flight ------------------------------------------------------

def _jwt_expiry(token: str) -> Optional[float]:
    """The `exp` claim of a JWT, or None if this is not one.

    No signature check and no library: we are reading a timestamp the token
    carries about itself, purely to warn earlier than the crawl would. A
    forged token is not the threat here — an expired one is.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    exp = claims.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def session_status(
    state: Optional[dict[str, Any]], target_url: Optional[str] = None,
) -> dict[str, Any]:
    """What a saved session says about its own lifetime, best-effort.

    Sessions expire, and finding out *after* a twenty-minute crawl of login
    screens is an expensive way to learn it.

    Only **bearer tokens for the target origin** are consulted. An earlier
    version took the earliest expiry across every credential in the file and
    promptly declared a working session dead: logging in through Google
    leaves that provider's cookies in the same storage state, and one of them
    had lapsed while the portal's own token had 16 hours left. Refusing to
    crawl on that basis would be the same crying-wolf failure this project
    keeps guarding against.

    Cookie-only sessions are reported as unknown rather than guessed at:
    which cookie actually carries the session is not knowable from disk, and
    the crawl's own H4 check is the backstop either way.
    """
    if not state:
        return {"known": False}

    want_host = urlparse(target_url).netloc.lower() if target_url else None

    best: Optional[tuple[float, str]] = None
    for origin in state.get("origins") or []:
        origin_host = urlparse(origin.get("origin", "")).netloc.lower()
        if want_host and origin_host and origin_host != want_host:
            continue
        for item in origin.get("localStorage") or []:
            exp = _jwt_expiry(str(item.get("value", "")))
            if exp is None:
                continue
            # The longest-lived token for this origin is the one that keeps
            # the session alive; a short-lived access token sitting beside a
            # refresh token does not end it.
            if best is None or exp > best[0]:
                best = (exp, f"{item.get('name')} for {origin_host or 'this app'}")

    if best is None:
        return {"known": False}

    when, source = best
    remaining = when - time.time()
    return {
        "known": True,
        "expires_at": when,
        "seconds_remaining": remaining,
        "expired": remaining <= 0,
        "source": source,
    }


def describe_session(
    state: Optional[dict[str, Any]], path: Optional[str],
    target_url: Optional[str] = None,
) -> list[str]:
    """Lines to print before a run. Empty when there is nothing worth saying."""
    if not state:
        return []
    status = session_status(state, target_url)
    if not status["known"]:
        # Nothing readable said when this expires. Say so plainly rather than
        # inferring from unrelated credentials in the same file.
        return [f"[INFO] Using session {path} (it records no expiry we can "
                f"read; a rejected session is still detected during the run)."]
    remaining = status["seconds_remaining"]
    if status["expired"]:
        return [
            f"[ERROR] The saved session {path} expired "
            f"{abs(remaining) / 3600:.1f}h ago ({status['source']}).",
            f"[ERROR] Re-capture it before crawling:  "
            f"python -m ui_discovery.login <login-url> --output {path}",
        ]
    if remaining < 1800:
        return [f"[WARN] Session {path} expires in {remaining / 60:.0f} minutes "
                f"({status['source']}) — it may lapse mid-crawl."]
    return [f"[INFO] Session {path} valid for another "
            f"{remaining / 3600:.1f}h ({status['source']})."]
