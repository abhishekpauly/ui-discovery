"""Auth tests — prove a saved session is actually applied.

A tiny cookie-gated server serves a login page to anonymous requests and a
protected dashboard when a session cookie is present. We then show that passing
the matching storage-state makes the engine see the protected content.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from ui_discovery.auth import load_storage_state
from ui_discovery.crawler import crawl_site
from ui_discovery.extraction import extract_page

PROTECTED = b"<!doctype html><title>Dashboard</title><body><main><h1>Secret Dashboard</h1><a href='/'>Home</a></main></body>"
LOGIN = b"<!doctype html><title>Login</title><body><main><h1>Please log in</h1></main></body>"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        cookie = self.headers.get("Cookie", "")
        authed = "ui_session=valid" in cookie
        body = PROTECTED if authed else LOGIN
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def _state_for(host_port: str) -> dict:
    return {
        "cookies": [{
            "name": "ui_session", "value": "valid",
            "domain": "127.0.0.1", "path": "/",
            "httpOnly": False, "secure": False, "sameSite": "Lax",
            "expires": -1,
        }],
        "origins": [],
    }


def test_without_session_sees_login(server):
    page = extract_page(f"{server}/dashboard")
    assert any("Please log in" == h.text for h in page.headings)


def test_with_session_sees_protected_content(server):
    page = extract_page(f"{server}/dashboard", auth_state=_state_for(server))
    assert any("Secret Dashboard" == h.text for h in page.headings)


def test_crawl_uses_session(server):
    crawl = asyncio.run(crawl_site(f"{server}/dashboard", max_depth=1,
                                   output_dir="/tmp/uidisco_auth",
                                   auth_state=_state_for(server)))
    titles = {n.page.title for n in crawl.pages}
    assert "Dashboard" in titles  # protected <title>, not "Login"


# --- storage-state loading --------------------------------------------------

def test_load_storage_state_roundtrip(tmp_path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps({"cookies": [], "origins": []}))
    assert load_storage_state(str(p)) == {"cookies": [], "origins": []}


def test_load_storage_state_none():
    assert load_storage_state(None) is None


def test_load_storage_state_missing():
    with pytest.raises(FileNotFoundError):
        load_storage_state("/no/such/session.json")


def test_load_storage_state_rejects_non_state(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"not": "a session"}))
    with pytest.raises(ValueError):
        load_storage_state(str(p))
