"""Async JSON-RPC client for the upstream dashboard WebSocket."""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed

from hermes_plus.adapter.protocol import READY_EVENT_NAME, TOKEN_QUERY_PARAMETER


class RpcError(Exception):
    """An error response returned by a JSON-RPC method."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"JSON-RPC error {code}: {message}")


def _authenticated_url(url: str, token: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != TOKEN_QUERY_PARAMETER]
    query.append((TOKEN_QUERY_PARAMETER, token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class GatewayClient:
    """Correlate concurrent JSON-RPC calls over one upstream WebSocket."""

    def __init__(self, ws_url: str, token: str) -> None:
        self.ws_url = _authenticated_url(ws_url, token)
        self.token = token
        self.ready: dict[str, Any] | None = None
        self._socket = None
        self._reader: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def connect(self) -> None:
        """Connect and consume the server's initial ``gateway.ready`` event."""
        if self._socket is not None:
            return
        self._socket = await websockets.connect(self.ws_url)
        try:
            first_frame = json.loads(await self._socket.recv())
        except BaseException:
            await self._socket.close()
            self._socket = None
            raise
        if not self._is_ready_frame(first_frame):
            await self._socket.close()
            self._socket = None
            raise ConnectionError("upstream did not send the expected gateway.ready event")
        self.ready = first_frame["params"]["payload"]
        self._reader = asyncio.create_task(self._read_frames(), name="hermes-plus-gateway-reader")

    @staticmethod
    def _is_ready_frame(frame: Any) -> bool:
        return (
            isinstance(frame, dict)
            and frame.get("method") == "event"
            and isinstance(frame.get("params"), dict)
            and frame["params"].get("type") == READY_EVENT_NAME
            and isinstance(frame["params"].get("payload"), dict)
        )

    async def _read_frames(self) -> None:
        try:
            async for raw in self._socket:
                frame = json.loads(raw)
                if isinstance(frame, dict) and "id" in frame and (
                    "result" in frame or "error" in frame
                ):
                    future = self._pending.pop(frame["id"], None)
                    if future is not None and not future.done():
                        error = frame.get("error")
                        if isinstance(error, dict):
                            future.set_exception(RpcError(
                                int(error.get("code", -32603)),
                                str(error.get("message", "unknown JSON-RPC error")),
                                error.get("data"),
                            ))
                        else:
                            future.set_result(frame.get("result"))
                elif isinstance(frame, dict) and frame.get("method") == "event":
                    await self._events.put(frame)
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception as exc:
            self._fail_pending(exc)
        finally:
            self._fail_pending(ConnectionError("upstream WebSocket disconnected"))
            await self._events.put(None)

    def _fail_pending(self, error: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 60) -> Any:
        """Call one RPC and return its result, raising :class:`RpcError` on a remote error."""
        if self._socket is None:
            await self.connect()
        request_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        try:
            await self._socket.send(json.dumps(request, separators=(",", ":")))
            return await asyncio.wait_for(future, timeout=timeout)
        except BaseException:
            self._pending.pop(request_id, None)
            raise

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield upstream event frames until the WebSocket closes."""
        if self._socket is None:
            await self.connect()
        while (frame := await self._events.get()) is not None:
            yield frame

    async def close(self) -> None:
        """Close the socket and stop its reader task."""
        socket = self._socket
        if socket is None:
            return
        self._socket = None
        await socket.close()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, timeout=5)
            except asyncio.TimeoutError:
                self._reader.cancel()
                await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        self._fail_pending(ConnectionError("GatewayClient is closed"))
        await self._events.put(None)
