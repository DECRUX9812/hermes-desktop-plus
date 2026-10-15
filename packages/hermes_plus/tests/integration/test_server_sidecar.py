"""End-to-end sidecar tests: real hermes-plus subprocess on top of real stock Hermes.

Runs `hermes-plus serve` on 127.0.0.1:93xx with a throwaway HERMES_HOME and a tiny real
ui_dir, then exercises auth, injection, the WS multiplexer (local and forwarded RPCs),
the HTTP proxy, and child-process reaping. The session-scoped `sidecar` fixture, the
Sidecar harness and the WS helpers live in tests/integration/conftest.py. Skips with a
reason when no stock Hermes binary is available or the dashboard cannot start here.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid

import httpx
import pytest
import websockets

from conftest import Sidecar, _dashboard_processes, _ws_call, _ws_open

pytestmark = pytest.mark.integration


def test_healthz_and_plus_info(sidecar: Sidecar):
    reply = httpx.get(f"{sidecar.url}/healthz", timeout=10)
    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "ok"
    assert "hermes_version" in body
    assert body["hermes_version"] or body["hermes_commit"]

    info = httpx.get(
        f"{sidecar.url}/api/plus/info",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert info.status_code == 200
    payload = info.json()
    assert payload["adapter_api"] == 1
    assert "hermes_version" in payload
    assert payload["compat"]["status"] in {"supported", "untested", "unsupported"}
    assert isinstance(payload["features"], dict)


def test_auth_token_flow(sidecar: Sidecar):
    with httpx.Client(follow_redirects=False, timeout=10) as client:
        assert client.get(f"{sidecar.url}/api/plus/info").status_code == 401
        assert client.get(f"{sidecar.url}/").status_code == 401

        login = client.get(f"{sidecar.url}/", params={"token": sidecar.token})
        assert login.status_code == 303
        assert "token=" not in login.headers["location"]
        cookie = login.headers["set-cookie"]
        assert "hermes_plus_session=" in cookie
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

        assert client.get(f"{sidecar.url}/").status_code == 200
        assert client.get(f"{sidecar.url}/api/plus/info").status_code == 200


@pytest.mark.asyncio
async def test_websocket_rejected_without_auth(sidecar: Sidecar):
    with pytest.raises(Exception) as excinfo:
        await websockets.connect(f"ws://127.0.0.1:{sidecar.port}/api/ws")
    assert type(excinfo.value).__name__ in {
        "InvalidStatus",
        "InvalidStatusCode",
        "ConnectionClosed",
        "ConnectionClosedError",
        "InvalidHandshake",
        "InvalidMessage",
    }


def test_index_injection_and_static_cache(sidecar: Sidecar):
    reply = httpx.get(
        f"{sidecar.url}/", headers={"X-Hermes-Session-Token": sidecar.token}, timeout=10
    )
    assert reply.status_code == 200
    html = reply.text
    assert f'window.__HERMES_SESSION_TOKEN__="{sidecar.token}";' in html
    assert "window.__HERMES_PLUS__={" in html
    assert "window.__HERMES_BASE_PATH__" in html
    upstream_tokens = [token for _, token in _dashboard_processes(sidecar.home) if token]
    assert upstream_tokens, "expected to discover the upstream dashboard token via /proc"
    for token in upstream_tokens:
        assert token not in html

    spa = httpx.get(
        f"{sidecar.url}/deep/client/route",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert spa.status_code == 200
    assert "__HERMES_SESSION_TOKEN__" in spa.text
    assert "no-cache" in spa.headers["cache-control"]

    asset = httpx.get(
        f"{sidecar.url}/assets/app-B12cD34e.js",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]

    missing = httpx.get(
        f"{sidecar.url}/assets/absent.js",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_stock_rpc_forwarded_over_multiplexer(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        reply = await _ws_call(ws, "config.get", {"key": "theme"})
    assert "error" not in reply, reply
    assert isinstance(reply["result"], dict)
    assert "value" in reply["result"]


@pytest.mark.asyncio
async def test_local_rpc_persists_across_restart(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        created = await _ws_call(
            ws, "bots_team.create", {"name": "Restart Demo", "mission": "Persist", "actor": "you"}
        )
        assert "error" not in created, created
        team_id = created["result"]["team"]["id"]
        listed = await _ws_call(ws, "bots_team.list")
        assert team_id in [team["id"] for team in listed["result"]["teams"]]

    sidecar.restart()
    assert httpx.get(f"{sidecar.url}/healthz", timeout=10).status_code == 200

    async with await _ws_open(sidecar) as ws:
        relisted = await _ws_call(ws, "bots_team.list")
    assert team_id in [team["id"] for team in relisted["result"]["teams"]]


@pytest.mark.asyncio
async def test_owned_method_reaches_upstream_with_namespaced_ids(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        # Force the profile store to exist, then journal a pending approval by hand
        # (a real SQLite write; the server picks it up through its own connection).
        await _ws_call(ws, "fork.onboarding.state")
        db_path = sidecar.home / "hermes-plus" / "plus.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO approvals (request_id, session_id, created_at, status) "
                "VALUES ('req-itest', 'sess-itest', 1.0, 'pending')"
            )

        frames: list[dict] = []
        request_id = f"t-{uuid.uuid4().hex[:8]}"
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "fork.approvals.decide",
            "params": {"session_id": "sess-itest", "request_id": "req-itest", "choice": "once"},
        }))
        reply = None
        async with asyncio.timeout(30):
            async for raw in ws:
                frame = json.loads(raw)
                frames.append(frame)
                if isinstance(frame, dict) and frame.get("id") == request_id:
                    reply = frame
                    break

        assert reply is not None
        assert not any(
            str(frame.get("id", "")).startswith("hp-") for frame in frames
        ), f"internal namespaced frame leaked to the client: {frames}"
        if "error" in reply:
            # Upstream has no such live session; its error must reach our request id.
            assert reply["error"]["code"] not in {-32601, -32603}
        else:
            assert reply["result"]["resolved"] is False
            assert reply["result"]["approval"]["status"] == "allowed"


def test_http_api_proxy(sidecar: Sidecar):
    reply = httpx.get(
        f"{sidecar.url}/api/health",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert reply.status_code == 200


def test_shutdown_reaps_dashboard_child(sidecar: Sidecar):
    children = _dashboard_processes(sidecar.home)
    assert children, "expected a running hermes dashboard child"
    sidecar.stop()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _dashboard_processes(sidecar.home):
        time.sleep(0.3)
    assert _dashboard_processes(sidecar.home) == []
