"""Parameter readers shared by handlers; they normalise input and raise the protocol's fault codes."""

from __future__ import annotations

from typing import Any

from hermes_plus.rpc.errors import PROFILE_REQUIRED, RpcFault


def read_text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    return "" if value is None else str(value).strip()


def require_text(params: dict[str, Any], key: str, code: int, message: str) -> str:
    value = read_text(params, key)
    if not value:
        raise RpcFault(code, message)
    return value


def profile_param(params: dict[str, Any]) -> str:
    profile = read_text(params, "profile") or read_text(params, "name")
    if not profile:
        raise RpcFault(PROFILE_REQUIRED, "profile required")
    return profile
