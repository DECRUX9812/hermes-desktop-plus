"""A keep-alive connection the upstream already closed must not become a 500.

The browser proof's failed-request log named one real 500: a plain
``GET /api/profiles/sessions`` proxied to the stock dashboard, where httpx raised
``RemoteProtocolError`` ("Server disconnected without sending a response") on a
pooled connection the upstream had closed. The request never reached the server,
so a bodiless method is replayed once on a fresh connection. The upstream here is
a real TCP server on loopback that drops the first connection; nothing is stubbed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from hermes_plus.server.proxy import proxy_http


class _FlakyUpstream:
    """A real HTTP server: drops the first connection, answers the next with 200."""

    def __init__(self, *, drop_every: bool = False) -> None:
        self.drop_every = drop_every
        self.connections = 0
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lowered = head.lower()
            if b"transfer-encoding: chunked" in lowered:
                # httpx streams an unknown-length body chunked; wait for the terminator
                # so the client finishes sending before the connection dies.
                await reader.readuntil(b"\r\n0\r\n\r\n")
            else:
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1].strip())
                if length:
                    await reader.readexactly(length)
        except (asyncio.IncompleteReadError, ConnectionResetError, ValueError):
            pass
        if self.drop_every or self.connections == 1:
            # The whole request arrived; the connection dies before any response.
            writer.close()
            return
        body = b'{"ok":true}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()


def _request(method: str, *, path: str, query: str, body: bytes = b"") -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"127.0.0.1:9230"), (b"accept", b"application/json")],
        "client": ("127.0.0.1", 45231),
        "server": ("127.0.0.1", 9230),
        "root_path": "",
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def _body(response: Any) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def test_a_dropped_pooled_connection_is_replayed_once() -> None:
    upstream = _FlakyUpstream()
    await upstream.start()
    try:
        async with httpx.AsyncClient() as client:
            runtime = SimpleNamespace(http_client=client, upstream_token="tok")
            request = _request("GET", path="/api/profiles/sessions", query="limit=100&profile=default")

            response = await proxy_http(
                request, runtime, f"http://127.0.0.1:{upstream.port}", auth_upstream=False
            )

            assert response.status_code == 200
            assert await _body(response) == b'{"ok":true}'
        assert upstream.connections == 2, "the dead connection must be replaced, not reported"
    finally:
        await upstream.stop()


async def test_a_body_method_is_never_replayed() -> None:
    """A POST may already have reached the upstream; a retry could apply it twice."""
    upstream = _FlakyUpstream(drop_every=True)
    await upstream.start()
    try:
        async with httpx.AsyncClient() as client:
            runtime = SimpleNamespace(http_client=client, upstream_token="tok")
            request = _request("POST", path="/api/chat", query="profile=default", body=b'{"text":"hi"}')

            with pytest.raises(httpx.RemoteProtocolError):
                await proxy_http(
                    request, runtime, f"http://127.0.0.1:{upstream.port}", auth_upstream=False
                )
        assert upstream.connections == 1
    finally:
        await upstream.stop()


async def test_a_dead_upstream_still_fails_the_second_time() -> None:
    """The replay must not hide a dashboard that is genuinely gone."""
    upstream = _FlakyUpstream()
    await upstream.start()
    port = upstream.port
    await upstream.stop()

    async with httpx.AsyncClient() as client:
        runtime = SimpleNamespace(http_client=client, upstream_token="tok")
        request = _request("GET", path="/api/profiles/sessions", query="profile=default")

        with pytest.raises(httpx.TransportError):
            await proxy_http(request, runtime, f"http://127.0.0.1:{port}", auth_upstream=False)
