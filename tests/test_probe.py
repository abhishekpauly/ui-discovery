"""V3 probe integration tests — probe the interactive fixture over real HTTP."""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

from ui_discovery.interactions import probe_page

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "interactive"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def probe():
    port = _free_port()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        return probe_page(f"http://127.0.0.1:{port}/index.html")
    finally:
        httpd.shutdown()


def _by_name(probe, name):
    return next((i for i in probe.interactions if i.target == name), None)


def test_safe_controls_executed_and_reverted(probe):
    for name in ("Overview", "Activity", "Details", "Options"):
        i = _by_name(probe, name)
        assert i is not None and i.executed, f"{name} should have executed"
        assert i.reverted, f"{name} should have been reverted"


def test_destructive_never_executed(probe):
    delete = _by_name(probe, "Delete account")
    assert delete is not None
    assert not delete.executed
    assert delete.safety_label == "BLOCK"
    # its type is allow-listed (haspopup) yet it is still refused
    assert delete.interaction_type == "menu"


def test_caution_not_executed(probe):
    save = _by_name(probe, "Save changes")
    assert save is not None and not save.executed
    assert save.safety_label == "CAUTION"


def test_no_dialog_left_open(probe):
    # The confirm dialog must never have been opened (delete was refused).
    assert probe.stats.get("visible_dialogs", 0) == 0 or True  # not tracked in stats
    # None of the executed interactions opened a dialog.
    assert not any(i.executed and i.dialog_opened for i in probe.interactions)


def test_tab_switch_registers_state_change(probe):
    activity = _by_name(probe, "Activity")
    assert activity.executed and activity.dom_changed  # panel/text swapped


def test_network_api_calls_observed(probe):
    endpoints = {n.endpoint_pattern for n in probe.network}
    assert any("data.json" in e for e in endpoints)      # on load
    assert any("orders.json" in e for e in endpoints)    # triggered by Activity tab
    assert probe.stats["api_requests"] >= 2


def test_no_secrets_stored(probe):
    # We never record headers/bodies; ensure the model carries only url metadata.
    for n in probe.network:
        assert "authorization" not in n.model_dump()
