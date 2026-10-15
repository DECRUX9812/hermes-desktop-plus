"""The bridge's loopback RPC route: POST /api/plus/bridge/rpc.

This is how the bridge plugin's tools reach sidecar state (bot teams, mailbox). Authentication is
ONLY the ``X-Hermes-Plus-Bridge`` token, constant-time compared — never a cookie, never a session
token — and the route answers loopback clients only. The path is registered as front-door-public
because it enforces its own stricter check here.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from hermes_plus.rpc import RpcContext, dispatch
from hermes_plus.rpc.context import GatewayUpstream
from hermes_plus.server.auth import PUBLIC_PATHS

log = logging.getLogger(__name__)

BRIDGE_ROUTE = "/api/plus/bridge/rpc"
BRIDGE_HEADER = "x-hermes-plus-bridge"
PUBLIC_PATHS.add(BRIDGE_ROUTE)


def _is_loopback_client(request: Request) -> bool:
    client = request.scope.get("client")
    host = str(client[0]) if client else ""
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def bridge_rpc(request: Request) -> JSONResponse:
    runtime = request.app.state.runtime
    if not _is_loopback_client(request):
        return JSONResponse({"error": "loopback only"}, status_code=403)
    supplied = request.headers.get(BRIDGE_HEADER, "")
    if not supplied or not hmac.compare_digest(supplied, runtime.auth.token):
        return JSONResponse({"error": "invalid bridge token"}, status_code=401)
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return JSONResponse({"error": "body must be {\"method\", \"params\"}"}, status_code=400)

    params = body.get("params")
    if not isinstance(params, dict):
        params = {}
    profile = runtime.settings.profile
    store = runtime.store_for(profile)
    context = RpcContext(
        store=store,
        upstream=GatewayUpstream(runtime.internal) if runtime.internal is not None else None,
        internal=GatewayUpstream(runtime.internal) if runtime.internal is not None else None,
        profile=profile,
        capabilities=runtime.capabilities,
    )
    reply = await dispatch(context, {
        "jsonrpc": "2.0", "id": 1, "method": body["method"], "params": params,
    })
    return JSONResponse(reply)
