"""Runtime.start must degrade — not die — when the upstream never answers probes.

The "upstream" here is a real ``websockets`` server on loopback that performs
the gateway.ready handshake and then deliberately never replies to any call:
the exact wedged-upstream case whose probe retries end in RuntimeError. Nothing
stands in for Hermes beyond that silence, and the stock binary is still used
for the real ``detect()`` subprocess run during startup.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
import websockets

from hermes_resolve import resolve_stock_hermes
from hermes_plus.adapter import capabilities as capabilities_module
from hermes_plus.server.app import build_app
from hermes_plus.server.runtime import Runtime
from hermes_plus.settings import ServeSettings


def _free_port() -> int:
    for port in range(9300, 9400):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("no free loopback port in the 9300-9399 test range")


async def _start_wedged_upstream(port: int) -> websockets.asyncio.server.Server:
    ready = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "gateway.ready", "payload": {"version": "wedged-demo"}},
        }
    )

    async def handler(connection: websockets.asyncio.server.ServerConnection) -> None:
        await connection.send(ready)
        async for _ in connection:
            pass  # swallow every request, never answer: the probe must time out

    return await websockets.serve(handler, "127.0.0.1", port)


async def test_probe_runtime_error_degrades_and_info_reports_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, _ = resolve_stock_hermes()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.skip(f"no stock Hermes binary for detect() during startup: {binary}")

    # Shrink the probe budget so the wedged upstream fails in tenths of a
    # second instead of the real 3x20s; the retry-then-RuntimeError path is
    # the code under test and runs for real.
    monkeypatch.setattr(capabilities_module, "PROBE_TIMEOUT", 0.1)
    monkeypatch.setattr(capabilities_module, "PROBE_ATTEMPTS", 2)

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    token_file = tmp_path / "upstream-token"
    token_file.write_text("demo-wedged-token", encoding="utf-8")

    upstream_port = _free_port()
    settings = ServeSettings(
        host="127.0.0.1",
        port=_free_port(),
        hermes_bin=str(binary),
        upstream_url=f"http://127.0.0.1:{upstream_port}",
        upstream_token_file=token_file,
    )
    runtime = Runtime(settings)
    app = build_app(settings, runtime)
    info_route = next(
        route for route in app.routes if getattr(route, "path", None) == "/api/plus/info"
    )

    wedged = await _start_wedged_upstream(upstream_port)
    try:
        await runtime.start()
        assert runtime.started, "serve must start even when the probe never gets an answer"
        assert runtime.capabilities.known is False

        response = await info_route.endpoint(None)
        payload = json.loads(response.body)
        assert payload["capabilities"] == "unknown"
    finally:
        wedged.close()
        await wedged.wait_closed()
        await runtime.stop()
