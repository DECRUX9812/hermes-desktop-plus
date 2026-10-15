"""fork.approvals.{list,decide}: the per-session journal of tool approval requests and their decisions."""

from __future__ import annotations

import asyncio
from typing import Any

from hermes_plus.rpc.context import RpcContext, UpstreamRpcError
from hermes_plus.rpc.errors import APPROVAL_NOT_FOUND, APPROVAL_PARAMS, RpcFault
from hermes_plus.rpc.params import read_text, require_text
from hermes_plus.rpc.registry import method
from hermes_plus.store.approvals import DECISION_CHOICES

UPSTREAM_SESSION_NOT_FOUND = 4001


@method("fork.approvals.list")
def list_approvals(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    session_id = require_text(params, "session_id", APPROVAL_PARAMS, "session_id required")
    return {"approvals": ctx.approvals.list_for_session(session_id)}


@method("fork.approvals.decide")
async def decide_approval(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    session_id = read_text(params, "session_id")
    request_id = read_text(params, "request_id")
    choice = params.get("choice")
    if not session_id or not request_id or not isinstance(choice, str) or choice not in DECISION_CHOICES:
        raise RpcFault(APPROVAL_PARAMS, "session_id, request_id and a valid choice required")
    row = await asyncio.to_thread(ctx.approvals.get, request_id)
    if row is None or row["session_id"] != session_id:
        raise RpcFault(APPROVAL_NOT_FOUND, "approval not found")

    resolved = False
    if ctx.upstream is not None:
        resolved = await _resolve_live(ctx, session_id, request_id, choice)
    approval = await asyncio.to_thread(ctx.approvals.decide, request_id, choice)
    return {"approval": approval, "resolved": resolved}


async def _resolve_live(ctx: RpcContext, session_id: str, request_id: str, choice: str) -> bool:
    try:
        reply = await ctx.upstream.call("approval.respond", {
            "session_id": session_id, "choice": choice, "request_id": request_id,
        })
    except UpstreamRpcError as err:
        if err.code == UPSTREAM_SESSION_NOT_FOUND:
            return False
        raise
    return bool(reply.get("resolved"))
