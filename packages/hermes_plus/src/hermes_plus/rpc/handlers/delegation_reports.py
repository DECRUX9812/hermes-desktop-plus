"""delegation.reports: the fork's settled-delegation report cards, sourced from the bridge spool."""

from __future__ import annotations

from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import APPROVAL_PARAMS, RpcFault
from hermes_plus.rpc.params import require_text
from hermes_plus.rpc.registry import method


@method("delegation.reports")
def list_reports(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    session_id = require_text(params, "session_id", APPROVAL_PARAMS, "session_id required")
    return {"reports": ctx.delegation_reports.list_for_session(session_id)}
