"""Method table and dispatch for the newline-delimited JSON-RPC 2.0 surface."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any

from hermes_plus.rpc.context import RpcContext, UpstreamRpcError
from hermes_plus.rpc.errors import (
    INTERNAL,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    RpcFault,
)

log = logging.getLogger(__name__)

NAMESPACES = (
    "fork.bot.identity", "fork.memory", "fork.onboarding", "fork.approvals", "fork.routines", "session",
    "bots_team", "bots_mailbox",
)

Handler = Callable[[RpcContext, dict[str, Any]], Any]
_handlers: dict[str, Handler] = {}


def method(name: str) -> Callable[[Handler], Handler]:
    if not name.startswith(tuple(f"{ns}." for ns in NAMESPACES)):
        raise ValueError(f"{name} is outside the plus RPC surface")

    def register(fn: Handler) -> Handler:
        if name in _handlers:
            raise ValueError(f"{name} is registered twice")
        _handlers[name] = fn
        return fn

    return register


def methods() -> tuple[str, ...]:
    return tuple(sorted(_handlers))


def _reply(rid: Any, *, result: Any = None, fault: RpcFault | None = None) -> str:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
    if fault is not None:
        body["error"] = fault.to_error()
    else:
        body["result"] = result
    try:
        return json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        log.exception("rpc reply for id %r is not serialisable", rid)
        return json.dumps({"jsonrpc": "2.0", "id": rid, "error": RpcFault(INTERNAL, "internal error").to_error()})


async def _dispatch(ctx: RpcContext, message: Any) -> str | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        rid = message.get("id") if isinstance(message, dict) else None
        return _reply(rid, fault=RpcFault(INVALID_REQUEST, "invalid request"))

    rid = message.get("id")
    notification = "id" not in message
    name = message["method"]
    try:
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RpcFault(INVALID_PARAMS, "params must be an object")
        handler = _handlers.get(name)
        if handler is None:
            raise RpcFault(METHOD_NOT_FOUND, f"unknown method {name}")
        if inspect.iscoroutinefunction(handler):
            result = await handler(ctx, params)
        else:
            result = await asyncio.to_thread(handler, ctx, params)
    except RpcFault as fault:
        failure: RpcFault | None = fault
    except UpstreamRpcError as err:
        failure = RpcFault(err.code, err.message, err.data)
    except Exception:
        log.exception("rpc method %s failed", name)
        failure = RpcFault(INTERNAL, "internal error")
    else:
        return None if notification else _reply(rid, result=result)
    return None if notification else _reply(rid, fault=failure)


async def handle_text(ctx: RpcContext, text: str) -> str | None:
    try:
        payload = json.loads(text)
    except ValueError:
        return _reply(None, fault=RpcFault(PARSE_ERROR, "parse error"))
    if isinstance(payload, list):
        if not payload:
            return _reply(None, fault=RpcFault(INVALID_REQUEST, "empty batch"))
        replies = [reply for reply in [await _dispatch(ctx, item) for item in payload] if reply is not None]
        return f"[{','.join(replies)}]" if replies else None
    return await _dispatch(ctx, payload)
