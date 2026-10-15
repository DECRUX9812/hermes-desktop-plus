"""WS upgrade tickets against a real sidecar (no mocks).

The renderer never puts the session token in a URL: it POSTs
/api/auth/ws-ticket (cookie-authenticated) and dials /api/ws?ticket=... .
These tests drive that flow over a real socket, including the rejections.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

from conftest import Sidecar

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _mint(sidecar: Sidecar) -> dict:
    reply = httpx.post(f"{sidecar.url}/api/auth/ws-ticket", headers=_auth(sidecar), timeout=30)
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_ticket_requires_a_credential(sidecar: Sidecar):
    reply = httpx.post(f"{sidecar.url}/api/auth/ws-ticket", timeout=30)
    assert reply.status_code == 401, reply.text


def test_ticket_is_accepted_once(sidecar: Sidecar):
    body = _mint(sidecar)
    assert body["ticket"]
    assert 0 < body["ttl_seconds"] <= 60

    async def dial(query: str):
        url = f"ws://127.0.0.1:{sidecar.port}/api/ws?{query}"
        async with websockets.connect(url) as ws:
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=20))

    first = asyncio.run(dial(f"ticket={body['ticket']}"))
    assert first.get("method") == "event"
    assert first["params"]["type"] == "gateway.ready"

    with pytest.raises(websockets.exceptions.InvalidStatus):
        asyncio.run(dial(f"ticket={body['ticket']}"))


def test_ticket_authorises_an_rpc_round_trip(sidecar: Sidecar):
    body = _mint(sidecar)

    async def call():
        url = f"ws://127.0.0.1:{sidecar.port}/api/ws?ticket={body['ticket']}"
        async with websockets.connect(url) as ws:
            await asyncio.wait_for(ws.recv(), timeout=20)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": "ticket-1", "method": "bots_team.list"}))
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                if reply.get("id") == "ticket-1":
                    return reply

    reply = asyncio.run(call())
    assert "result" in reply, reply


def test_the_session_cookie_authenticates_an_upgrade(sidecar: Sidecar):
    """The browser sends the front-door cookie on the upgrade as well."""

    async def dial():
        url = f"ws://127.0.0.1:{sidecar.port}/api/ws"
        headers = {"Cookie": f"hermes_plus_session={sidecar.token}"}
        async with websockets.connect(url, additional_headers=headers) as ws:
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=20))

    ready = asyncio.run(dial())
    assert ready.get("method") == "event"
    assert ready["params"]["type"] == "gateway.ready"


def test_a_bogus_ticket_is_refused(sidecar: Sidecar):
    async def dial():
        url = f"ws://127.0.0.1:{sidecar.port}/api/ws?ticket=nope"
        async with websockets.connect(url) as ws:
            await asyncio.wait_for(ws.recv(), timeout=20)

    with pytest.raises(websockets.exceptions.InvalidStatus):
        asyncio.run(dial())
