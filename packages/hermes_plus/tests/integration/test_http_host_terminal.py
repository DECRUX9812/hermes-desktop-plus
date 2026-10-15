"""Host-terminal tests: OUR ptyprocess PTY speaking the fork frame protocol.

The real WS test runs `echo hp-ok` in the shell and reads `hp-ok`; with the
setting disabled the WS is refused (close 4403).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import websockets

from conftest import HERMES_BIN, free_port, make_sidecar

pytestmark = pytest.mark.integration

META_PREFIX = "\0HERMES_TERMINAL_META:"


async def _terminal_url(sidecar: Sidecar, **query: str) -> str:
    base = f"ws://127.0.0.1:{sidecar.port}/api/host-terminal"
    params = "&".join(f"{key}={value}" for key, value in query.items())
    # Auth rides the session header via the WS handshake cookie jar? The
    # middleware reads headers, so pass the token as the standard query auth
    # the WS proxy path accepts.
    return f"{base}?{params}&token={sidecar.token}"


async def test_host_terminal_echo(sidecar: Sidecar, tmp_path: Path):
    url = await _terminal_url(sidecar, cols="80", rows="24", cwd=str(tmp_path))
    async with websockets.connect(url, open_timeout=30) as ws:
        meta_raw = await asyncio.wait_for(ws.recv(), timeout=30)
        assert meta_raw.startswith(META_PREFIX)
        meta = json.loads(meta_raw[len(META_PREFIX):])
        assert meta["shell"].strip()
        assert meta["reconnected"] is False
        assert meta["terminalId"]

        await ws.send("echo hp-ok\n")
        collected = ""
        for _ in range(200):
            frame = await asyncio.wait_for(ws.recv(), timeout=30)
            if frame.startswith(META_PREFIX):
                continue
            collected += frame
            if "hp-ok" in collected:
                break
        assert "hp-ok" in collected, collected[-2000:]


async def test_host_terminal_disabled_refused(tmp_path_factory: pytest.TempPathFactory):
    if not HERMES_BIN.exists():
        pytest.skip(f"stock Hermes binary not available at {HERMES_BIN}")
    server = make_sidecar(
        tmp_path_factory.mktemp("hermes-home-term-off"),
        env_extra={"HERMES_PLUS_HOST_TERMINAL": "0"},
    )
    try:
        url = await _terminal_url(server, cols="80", rows="24", cwd=str(server.home))
        ws = await websockets.connect(url, open_timeout=30)
        try:
            # The route accepts (handshake completes) then closes with 4403.
            closing = await asyncio.wait_for(ws.recv(), timeout=15)
            raise AssertionError(f"expected a close frame, got: {closing!r}")
        except websockets.exceptions.ConnectionClosed as exc:
            assert exc.rcvd is not None and exc.rcvd.code == 4403, exc
        finally:
            await ws.close()
    finally:
        server.stop()
