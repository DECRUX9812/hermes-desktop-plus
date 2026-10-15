"""fork.bot.identity.{get,set}: display name, role, instructions and model pin per bot profile."""

from __future__ import annotations

from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import EMPTY_DISPLAY_NAME, IDENTITY_CHANGED, INVALID_PARAMS, RpcFault
from hermes_plus.rpc.params import profile_param, read_text
from hermes_plus.rpc.registry import method
from hermes_plus.store import RevisionConflictError


@method("fork.bot.identity.get")
def get_identity(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return ctx.identities.get(profile_param(params)).to_wire()


@method("fork.bot.identity.set")
def set_identity(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = profile_param(params)
    changes: dict[str, str] = {}
    if params.get("display_name") is not None:
        display_name = read_text(params, "display_name")
        if not display_name:
            raise RpcFault(EMPTY_DISPLAY_NAME, "empty display_name")
        changes["display_name"] = display_name
    if params.get("role") is not None:
        changes["role"] = read_text(params, "role")
    if params.get("instructions") is not None:
        changes["instructions"] = str(params["instructions"])
    if params.get("provider") is not None:
        changes["model_provider"] = read_text(params, "provider")
    if params.get("model") is not None:
        changes["model_default"] = read_text(params, "model")

    expected = params.get("expected_revision")
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
        raise RpcFault(INVALID_PARAMS, "expected_revision must be an integer")
    try:
        identity = ctx.identities.update(profile, changes, expected_revision=expected)
    except RevisionConflictError as err:
        raise RpcFault(IDENTITY_CHANGED, "bot identity changed") from err
    return identity.to_wire()
