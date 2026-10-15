"""fork.memory.{list,add,update,remove}: the profile's real MEMORY.md and USER.md, each mutation returning a fresh list."""

from __future__ import annotations

from typing import Any

from hermes_plus.adapter import list_profiles, memory
from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import MEMORY_ITEM_CHANGED, MEMORY_REJECTED, PROFILE_NOT_FOUND, RpcFault
from hermes_plus.rpc.params import profile_param, read_text
from hermes_plus.rpc.registry import method


def _profile(params: dict[str, Any]) -> str:
    profile = profile_param(params)
    if profile not in list_profiles():
        raise RpcFault(PROFILE_NOT_FOUND, f"profile not found: {profile}")
    return profile


def _listing(profile: str) -> dict[str, Any]:
    snapshot = memory.snapshot(profile)
    return {
        "entries": [{"id": e.id, "target": e.target, "text": e.text} for e in snapshot["entries"]],
        "usage": snapshot["usage"],
    }


@method("fork.memory.list")
def list_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    try:
        return _listing(profile)
    except memory.MemoryRefused as err:
        raise RpcFault(MEMORY_REJECTED, str(err)) from err


@method("fork.memory.add")
def add_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    try:
        memory.add(profile, read_text(params, "target"), read_text(params, "text"))
    except memory.MemoryRefused as err:
        raise RpcFault(MEMORY_REJECTED, str(err)) from err
    return _listing(profile)


@method("fork.memory.update")
def update_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    try:
        memory.replace(profile, read_text(params, "id"), read_text(params, "text"))
    except memory.MemoryItemChanged as err:
        raise RpcFault(MEMORY_ITEM_CHANGED, "memory item changed") from err
    except memory.MemoryRefused as err:
        raise RpcFault(MEMORY_REJECTED, str(err)) from err
    return _listing(profile)


@method("fork.memory.remove")
def remove_memory(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    try:
        memory.remove(profile, read_text(params, "id"))
    except memory.MemoryItemChanged as err:
        raise RpcFault(MEMORY_ITEM_CHANGED, "memory item changed") from err
    except memory.MemoryRefused as err:
        raise RpcFault(MEMORY_REJECTED, str(err)) from err
    return _listing(profile)
