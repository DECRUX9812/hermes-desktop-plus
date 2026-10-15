"""fork.memory.{list,add,update,remove}: the per-profile memory and user notes, each mutation returning a fresh list."""

from __future__ import annotations

from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import MEMORY_ITEM_CHANGED, MEMORY_REJECTED, RpcFault
from hermes_plus.rpc.params import profile_param, read_text
from hermes_plus.rpc.registry import method
from hermes_plus.store import MemoryItemChanged, MemoryRejected


def _listing(ctx: RpcContext, profile: str) -> dict[str, Any]:
    snapshot = ctx.memory.snapshot(profile)
    return {
        "entries": [{"id": e.id, "target": e.target, "text": e.text} for e in snapshot["entries"]],
        "usage": snapshot["usage"],
    }


@method("fork.memory.list")
def list_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return _listing(ctx, profile_param(params))


@method("fork.memory.add")
def add_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = profile_param(params)
    try:
        ctx.memory.add(profile, read_text(params, "target"), read_text(params, "text"))
    except MemoryRejected as err:
        raise RpcFault(MEMORY_REJECTED, str(err)) from err
    return _listing(ctx, profile)


@method("fork.memory.update")
def update_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = profile_param(params)
    try:
        ctx.memory.replace(profile, read_text(params, "id"), read_text(params, "text"))
    except MemoryItemChanged as err:
        raise RpcFault(MEMORY_ITEM_CHANGED, "memory item changed") from err
    except MemoryRejected as err:
        raise RpcFault(MEMORY_REJECTED, str(err)) from err
    return _listing(ctx, profile)


@method("fork.memory.remove")
def remove_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = profile_param(params)
    try:
        ctx.memory.remove(profile, read_text(params, "id"))
    except MemoryItemChanged as err:
        raise RpcFault(MEMORY_ITEM_CHANGED, "memory item changed") from err
    return _listing(ctx, profile)
