"""The browser/upstream JSON-RPC WebSocket multiplexer."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import secrets
from typing import Any
from urllib.parse import urlsplit

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from hermes_plus.rpc import RpcContext, UpstreamRpcError, dispatch, owns
from hermes_plus.rpc.context import GatewayUpstream
from hermes_plus.server.proxy import authenticated_url
from hermes_plus.server.taps import TapDispatcher

log = logging.getLogger(__name__)


class _UpstreamLink:
    def __init__(self, socket: Any, send_lock: asyncio.Lock) -> None:
        self.socket = socket
        self.send_lock = send_lock
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._nonce = secrets.token_hex(6)

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = f"hp-{self._nonce}-{next(self._ids)}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with self.send_lock:
                await self.socket.send(json.dumps(frame, separators=(",", ":")))
            response = await future
        except BaseException:
            self.pending.pop(request_id, None)
            raise
        if "error" in response:
            error = response["error"]
            raise UpstreamRpcError(
                int(error.get("code", -32603)),
                str(error.get("message", "unknown upstream error")),
                error.get("data"),
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def resolve(self, frame: dict[str, Any]) -> bool:
        request_id = frame.get("id")
        if not isinstance(request_id, str):
            return False
        future = self.pending.pop(request_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(frame)
        return True

    def fail_pending(self, error: BaseException) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()


def _profile(value: Any, fallback: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return fallback
    normalized = value.strip()
    return fallback if normalized.casefold() in {"default", "current"} else normalized


async def _send_json(websocket: WebSocket, lock: asyncio.Lock, frame: dict[str, Any]) -> None:
    async with lock:
        await websocket.send_text(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))


async def _dispatch_local(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    runtime: Any,
    link: _UpstreamLink,
    connection_profile: str | None,
    request: dict[str, Any],
) -> None:
    request_params = request.get("params")
    params = request_params if isinstance(request_params, dict) else {}
    profile = _profile(params.get("profile"), connection_profile)
    if profile and "profile" not in params:
        params = {**params, "profile": profile}
        request = {**request, "params": params}
    store = await asyncio.to_thread(runtime.store_for, profile)
    context = RpcContext(
        store=store,
        upstream=link,
        profile=profile,
        capabilities=runtime.capabilities,
        internal=GatewayUpstream(runtime.internal) if runtime.internal is not None else None,
        mcp=runtime.mcp,
    )

    if hasattr(runtime, "emit"):
        context.emit = runtime.emit
    else:
        async def emit(event_name: str, payload: dict[str, Any], session_id: str | None = None) -> None:
            event_params: dict[str, Any] = {"type": event_name, "payload": payload}
            if session_id:
                event_params["session_id"] = session_id
            await _send_json(
                websocket,
                send_lock,
                {"jsonrpc": "2.0", "method": "event", "params": event_params},
            )

        context.emit = emit
    response = await dispatch(context, request)
    if response is not None:
        await _send_json(websocket, send_lock, response)


async def handle_websocket(websocket: WebSocket, runtime: Any) -> None:
    """Authenticate upstream with its token, then route or forward browser frames."""
    connection_profile = _profile(websocket.query_params.get("profile"), runtime.settings.profile)
    upstream_url = authenticated_url(
        runtime.upstream_ws_url,
        runtime.upstream_token,
        list(websocket.query_params.multi_items()),
    )
    parts = urlsplit(runtime.upstream_ws_url)
    origin_scheme = "https" if parts.scheme == "wss" else "http"
    try:
        upstream = await websockets.connect(
            upstream_url,
            origin=f"{origin_scheme}://{parts.netloc}",
        )
    except Exception:
        await websocket.accept()
        await websocket.close(code=1012, reason="Upstream WebSocket unavailable")
        return

    await websocket.accept()
    send_lock = asyncio.Lock()
    client_entry = (websocket, send_lock)
    if hasattr(runtime, "register_client"):
        runtime.register_client(client_entry)
    link = _UpstreamLink(upstream, send_lock)
    taps = TapDispatcher(runtime, connection_profile)
    local_tasks: set[asyncio.Task[None]] = set()
    failure = False

    async def client_to_upstream() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            raw = text if text is not None else message.get("bytes")
            if raw is None:
                continue
            if text is not None:
                try:
                    frame = json.loads(text)
                except (TypeError, ValueError):
                    frame = None
                if isinstance(frame, dict):
                    taps.offer(frame)
                    method_name = frame.get("method")
                    if isinstance(method_name, str) and owns(method_name, capabilities=runtime.capabilities):
                        task = asyncio.create_task(
                            _dispatch_local(
                                websocket, send_lock, runtime, link, connection_profile, frame
                            ),
                            name="hermes-plus-local-rpc",
                        )
                        local_tasks.add(task)
                        task.add_done_callback(local_tasks.discard)
                        continue
                elif isinstance(frame, list):
                    local_items = []
                    forwarded = []
                    for item in frame:
                        if (
                            isinstance(item, dict)
                            and isinstance(item.get("method"), str)
                            and owns(item["method"], capabilities=runtime.capabilities)
                        ):
                            local_items.append(item)
                        else:
                            forwarded.append(item)
                    if local_items:
                        for item in local_items:
                            task = asyncio.create_task(
                                _dispatch_local(
                                    websocket, send_lock, runtime, link, connection_profile, item
                                ),
                                name="hermes-plus-local-rpc",
                            )
                            local_tasks.add(task)
                            task.add_done_callback(local_tasks.discard)
                        if not forwarded:
                            continue
                        raw = json.dumps(forwarded, ensure_ascii=False, separators=(",", ":"))
                await upstream.send(raw)
            else:
                await upstream.send(raw)

    async def upstream_to_client() -> None:
        async for raw in upstream:
            if isinstance(raw, str):
                try:
                    frame = json.loads(raw)
                except ValueError:
                    frame = None
                if isinstance(frame, dict):
                    if link.resolve(frame):
                        continue
                    if frame.get("method") == "event":
                        taps.offer(frame)
            async with send_lock:
                if isinstance(raw, bytes):
                    await websocket.send_bytes(raw)
                else:
                    await websocket.send_text(raw)

    client_task = asyncio.create_task(client_to_upstream(), name="plus-rpc-client-pump")
    upstream_task = asyncio.create_task(upstream_to_client(), name="plus-rpc-upstream-pump")
    try:
        done, pending = await asyncio.wait(
            {client_task, upstream_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        failure = upstream_task in done
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except (WebSocketDisconnect, ConnectionClosed, asyncio.CancelledError):
                pass
            except Exception:
                failure = True
                log.exception("WebSocket multiplexer pump failed")
    finally:
        client_task.cancel()
        upstream_task.cancel()
        await asyncio.gather(client_task, upstream_task, return_exceptions=True)
        link.fail_pending(ConnectionError("upstream WebSocket disconnected"))
        for task in tuple(local_tasks):
            task.cancel()
        await asyncio.gather(*local_tasks, return_exceptions=True)
        await upstream.close()
        try:
            await websocket.close(code=1012 if failure else 1000)
        except RuntimeError:
            pass
        if hasattr(runtime, "unregister_client"):
            runtime.unregister_client(client_entry)
        await taps.close()
