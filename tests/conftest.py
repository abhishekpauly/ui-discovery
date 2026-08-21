"""Shared test helpers — a local static HTTP server on an ephemeral port."""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, directory: Path, port: int | None = None):
        # An explicit port lets a test serve two different directories at the
        # *same* origin in sequence — needed when comparing two snapshots,
        # since page URLs (and the fingerprints that embed them) would
        # otherwise differ purely because the port moved.
        self.port = port or _free_port()
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(directory)
        )
        # Quieten the default request logging.
        handler.log_message = lambda *a, **k: None  # type: ignore
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._stopped = False
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def stop(self) -> None:
        # Idempotent: a test may stop a server explicitly (to free its port for
        # the next one) and the fixture will stop it again at teardown.
        if self._stopped:
            return
        self._stopped = True
        self._httpd.shutdown()      # ends serve_forever
        self._httpd.server_close()  # releases the socket, freeing the port


@pytest.fixture
def serve():
    servers: list[Server] = []

    def _serve(rel_dir: str, port: int | None = None) -> Server:
        s = Server(ROOT / rel_dir, port=port)
        servers.append(s)
        return s

    yield _serve
    for s in servers:
        s.stop()


# --- browser marking --------------------------------------------------------
#
# CI runs a ~60s "fast lane" that must not launch Chromium, and a full sharded
# suite that may. Marking 28 files by hand would rot the moment someone adds a
# test; deriving the mark from what a module actually imports keeps it true
# without anybody maintaining a list.

_BROWSER_IMPORTS = (
    "extract_page", "crawl_site", "probe_page", "sync_playwright",
    "async_playwright",
)


def pytest_collection_modifyitems(config, items):
    """Mark every test in a module that reaches for a browser.

    A module counts as browser-dependent if it imports one of the entry points
    that launches Chromium, or if the test uses the `serve` fixture (a local
    HTTP server exists to be crawled). Both are cheap, textual signals — no
    import side effects, no collection cost worth measuring.
    """
    cache: dict[str, bool] = {}

    def needs_browser(item) -> bool:
        path = str(getattr(item, "fspath", "") or "")
        if path not in cache:
            try:
                source = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                source = ""
            cache[path] = any(name in source for name in _BROWSER_IMPORTS)
        if cache[path]:
            return True
        return "serve" in getattr(item, "fixturenames", ())

    for item in items:
        if needs_browser(item):
            item.add_marker(pytest.mark.browser)
