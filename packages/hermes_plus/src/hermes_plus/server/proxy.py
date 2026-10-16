"""Streaming HTTP and WebSocket proxies to the upstream dashboard."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import websockets
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})
_CLIENT_AUTH_HEADERS = frozenset({"cookie", "authorization", "x-hermes-session-token"})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _connection_tokens(headers: Any) -> set[str]:
    value = headers.get("connection", "")
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in {"https", "wss"} else "http"
    return f"{scheme}://{parts.netloc}"


def _has_dot_segment(path: str) -> bool:
    return any(segment in (".", "..") for segment in path.split("/"))


def authenticated_url(url: str, token: str, extra_query: list[tuple[str, str]] | None = None) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != "token"]
    query.extend(extra_query or [])
    query.append(("token", token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def websocket_url(runtime: Any, path: str, query: list[tuple[str, str]] | None = None) -> str:
    parts = urlsplit(runtime.upstream_ws_url)
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        path,
        urlencode([(k, v) for k, v in (query or []) if k != "token"] + [("token", runtime.upstream_token)]),
        "",
    ))


async def _send_upstream(client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], content: Any) -> httpx.Response:
    """Send once, replaying a bodiless request if the pooled connection was dead.

    The upstream dashboard closes keep-alive connections on its own schedule, so a
    pooled one can be gone before we reuse it: httpx then raises
    ``RemoteProtocolError`` ("Server disconnected without sending a response") with
    the request never delivered, which surfaced as a 500 on ordinary reads (the
    proof caught one on ``GET /api/profiles/sessions``). A fresh connection is the
    whole fix. Only methods with no body are replayed — a POST may have reached the
    upstream, and a genuinely dead dashboard still fails on the second attempt.
    """
    outbound = client.build_request(method, url, headers=headers, content=content)
    try:
        return await client.send(outbound, stream=True)
    except httpx.RemoteProtocolError:
        if content is not None:
            raise
        fresh = client.build_request(method, url, headers=headers)
        return await client.send(fresh, stream=True)


async def proxy_http(request: Request, runtime: Any, target_origin: str, *, auth_upstream: bool) -> Response:
    """Stream a request and response without buffering either body."""
    client: httpx.AsyncClient = runtime.http_client
    incoming = request.headers
    excluded = (
        _HOP_HEADERS
        | _CLIENT_AUTH_HEADERS
        | _connection_tokens(incoming)
        | {"host", "origin", "content-length"}
    )
    headers = {key: value for key, value in incoming.items() if key.lower() not in excluded}
    target = urlsplit(target_origin)
    origin = f"{target.scheme}://{target.netloc}"
    headers["Host"] = target.netloc
    headers["Origin"] = origin
    if auth_upstream:
        headers["X-Hermes-Session-Token"] = runtime.upstream_token

    path = request.url.path
    if _has_dot_segment(path):
        # The URL we hand to httpx is normalised, so ``/api/share/view/../../x``
        # would be sent as ``/x`` — a path this sidecar never routed. Refuse the
        # dot segment instead of letting normalisation rewrite the target.
        return JSONResponse({"detail": "Invalid path"}, status_code=400)
    query_items = [
        (key, value)
        for key, value in parse_qsl(request.url.query, keep_blank_values=True)
        if key != "token"
    ]
    query = urlencode(query_items)
    target_url = f"{origin}{path}" + (f"?{query}" if query else "")
    content = request.stream() if request.method.upper() in _BODY_METHODS else None
    upstream = await _send_upstream(client, request.method, target_url, headers, content)

    response_hops = _HOP_HEADERS | _connection_tokens(upstream.headers)
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in response_hops
    }

    async def chunks() -> AsyncIterator[bytes]:
        async for chunk in upstream.aiter_raw():
            yield chunk

    return StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )


async def proxy_websocket(websocket: WebSocket, runtime: Any, path: str) -> None:
    query = list(websocket.query_params.multi_items())
    url = websocket_url(runtime, path, query)
    try:
        upstream = await websockets.connect(url, origin=_origin(runtime.upstream_ws_url))
    except Exception:
        await websocket.close(code=1012, reason="Upstream WebSocket unavailable")
        return

    await websocket.accept()

    async def client_to_upstream() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("bytes") is not None:
                await upstream.send(message["bytes"])

    async def upstream_to_client() -> None:
        async for message in upstream:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)

    client_task = asyncio.create_task(client_to_upstream(), name="plus-ws-client-pump")
    upstream_task = asyncio.create_task(upstream_to_client(), name="plus-ws-upstream-pump")
    tasks = {client_task, upstream_task}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    failed_upstream = upstream_task in done
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        try:
            task.result()
        except (WebSocketDisconnect, ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            failed_upstream = True
    await upstream.close()
    try:
        await websocket.close(code=1012 if failed_upstream else 1000)
    except RuntimeError:
        pass
