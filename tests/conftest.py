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
    def __init__(self, directory: Path):
        self.port = _free_port()
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(directory)
        )
        # Quieten the default request logging.
        handler.log_message = lambda *a, **k: None  # type: ignore
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def stop(self) -> None:
        self._httpd.shutdown()


@pytest.fixture
def serve():
    servers: list[Server] = []

    def _serve(rel_dir: str) -> Server:
        s = Server(ROOT / rel_dir)
        servers.append(s)
        return s

    yield _serve
    for s in servers:
        s.stop()
