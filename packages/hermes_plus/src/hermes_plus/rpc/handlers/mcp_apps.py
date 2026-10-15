"""MCP Apps JSON-RPC handlers (mcp_apps.*)."""

from __future__ import annotations

import logging
from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import (
    MCP_APP_FAILED,
    MCP_APP_PARAM_OR_LOOKUP,
    MCP_APP_PERMISSION,
    RpcFault,
)
from hermes_plus.rpc.registry import method

log = logging.getLogger(__name__)


def _mcp_str(params: dict[str, Any], key: str) -> str:
    val = str(params.get(key) or "").strip()
    if not val:
        raise RpcFault(MCP_APP_PARAM_OR_LOOKUP, f"{key} required")
    return val


@method("mcp_apps.list")
async def mcp_apps_list(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = params.get("profile") or ctx.profile
    try:
        apps = await ctx.mcp_manager.list_apps(profile=profile)
        return {"apps": apps}
    except PermissionError as exc:
        raise RpcFault(MCP_APP_PERMISSION, str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise RpcFault(MCP_APP_PARAM_OR_LOOKUP, str(exc)) from exc
    except RpcFault:
        raise
    except Exception as exc:
        log.exception("mcp_apps.list failed")
        raise RpcFault(MCP_APP_FAILED, str(exc)) from exc


@method("mcp_apps.read_ui")
async def mcp_apps_read_ui(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    server = _mcp_str(params, "server")
    uri = _mcp_str(params, "uri")
    profile = params.get("profile") or ctx.profile
    try:
        return await ctx.mcp_manager.read_ui(profile=profile, server_name=server, uri=uri)
    except PermissionError as exc:
        raise RpcFault(MCP_APP_PERMISSION, str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise RpcFault(MCP_APP_PARAM_OR_LOOKUP, str(exc)) from exc
    except RpcFault:
        raise
    except Exception as exc:
        log.exception("mcp_apps.read_ui failed")
        raise RpcFault(MCP_APP_FAILED, str(exc)) from exc


@method("mcp_apps.call")
async def mcp_apps_call(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    server = _mcp_str(params, "server")
    tool = _mcp_str(params, "tool")
    args = params.get("arguments")
    arguments = args if isinstance(args, dict) else {}
    profile = params.get("profile") or ctx.profile
    try:
        return await ctx.mcp_manager.call_app_tool(
            profile=profile, server_name=server, tool_name=tool, arguments=arguments
        )
    except PermissionError as exc:
        raise RpcFault(MCP_APP_PERMISSION, str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise RpcFault(MCP_APP_PARAM_OR_LOOKUP, str(exc)) from exc
    except RpcFault:
        raise
    except Exception as exc:
        log.exception("mcp_apps.call failed")
        raise RpcFault(MCP_APP_FAILED, str(exc)) from exc
