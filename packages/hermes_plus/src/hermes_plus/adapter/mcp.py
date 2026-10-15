"""Adapter for Model Context Protocol (MCP) clients and applications."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

from hermes_plus.adapter.config import load_config

log = logging.getLogger(__name__)

UI_SCHEME = "ui://"
MAX_UI_BYTES = 5 * 1024 * 1024
MAX_APP_RESULT_CHARS = 2 * 1024 * 1024
DEFAULT_VISIBILITY = ("model", "app")
APP_MIME_TYPE = "text/html;profile=mcp-app"


def _tool_meta(tool: Any) -> dict[str, Any]:
    meta = getattr(tool, "meta", None)
    if isinstance(meta, dict):
        return meta
    dump = getattr(tool, "model_dump", None)
    if callable(dump):
        d = dump(by_alias=True)
        meta = d.get("meta") or d.get("_meta")
        if isinstance(meta, dict):
            return meta
    return {}


def tool_ui(tool: Any) -> dict[str, Any]:
    """The tool's app descriptor: {resourceUri, visibility, entrypoints} or {} when no UI."""
    meta = _tool_meta(tool)
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    uri = ui.get("resourceUri") or meta.get("ui/resourceUri") or meta.get("openai/outputTemplate")
    visibility = ui.get("visibility")
    if isinstance(visibility, list):
        visibility = [v for v in visibility if isinstance(v, str)]
    else:
        visibility = None
    openai_ui = meta.get("openai/ui") if isinstance(meta.get("openai/ui"), dict) else {}
    entrypoints = [
        e for e in openai_ui.get("entrypoints") or [] if isinstance(e, dict) and e.get("type")
    ]
    if not (isinstance(uri, str) and uri.startswith(UI_SCHEME)) and visibility is None:
        return {}
    out: dict[str, Any] = {"visibility": list(visibility or DEFAULT_VISIBILITY)}
    if isinstance(uri, str) and uri.startswith(UI_SCHEME):
        out["resourceUri"] = uri
    if entrypoints:
        out["entrypoints"] = entrypoints
    return out


def model_visible(tool: Any) -> bool:
    """True if tool visibility includes 'model' (default True)."""
    return "model" in (tool_ui(tool).get("visibility") or DEFAULT_VISIBILITY)


def _jsonable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True, exclude_none=True)
    return value


@dataclass
class McpConnection:
    server_name: str
    session: ClientSession
    stack: AsyncExitStack
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tools: list[Any] = field(default_factory=list)


class McpClientManager:
    """Manages MCP client connections configured in profile configs."""

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout
        self._connections: dict[tuple[str | None, str], McpConnection] = {}
        self._lock = asyncio.Lock()

    def _get_server_configs(self, profile: str | None = None) -> dict[str, dict[str, Any]]:
        config = load_config(profile)
        servers = config.get("mcp_servers")
        if not isinstance(servers, dict):
            servers = config.get("mcp", {}).get("servers")
        if not isinstance(servers, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for name, srv_cfg in servers.items():
            if not isinstance(srv_cfg, dict):
                continue
            if srv_cfg.get("enabled") is False:
                continue
            result[str(name)] = srv_cfg
        return result

    async def get_connection(self, profile: str | None, server_name: str) -> McpConnection:
        key = (profile, server_name)
        async with self._lock:
            conn = self._connections.get(key)
            if conn is not None:
                return conn

            server_configs = self._get_server_configs(profile)
            srv_cfg = server_configs.get(server_name)
            if srv_cfg is None:
                raise LookupError(f"MCP server '{server_name}' is not configured or is disabled")

            stack = AsyncExitStack()
            try:
                if "command" in srv_cfg:
                    command = str(srv_cfg["command"])
                    args = srv_cfg.get("args") or []
                    if isinstance(args, str):
                        args = [args]
                    else:
                        args = [str(a) for a in args]
                    env_extra = srv_cfg.get("env")
                    env: dict[str, str] | None = None
                    if isinstance(env_extra, dict):
                        env = {**os.environ, **{str(k): str(v) for k, v in env_extra.items()}}
                    server_params = StdioServerParameters(command=command, args=args, env=env)
                    read_stream, write_stream = await asyncio.wait_for(
                        stack.enter_async_context(stdio_client(server_params)),
                        timeout=self.timeout,
                    )
                elif "url" in srv_cfg:
                    url = str(srv_cfg["url"])
                    headers = srv_cfg.get("headers")
                    read_stream, write_stream = await asyncio.wait_for(
                        stack.enter_async_context(sse_client(url, headers=headers)),
                        timeout=self.timeout,
                    )
                else:
                    raise ValueError(f"MCP server '{server_name}' config must have 'command' or 'url'")

                session = await asyncio.wait_for(
                    stack.enter_async_context(ClientSession(read_stream, write_stream)),
                    timeout=self.timeout,
                )
                await asyncio.wait_for(session.initialize(), timeout=self.timeout)

                tools_res = await asyncio.wait_for(session.list_tools(), timeout=self.timeout)
                conn = McpConnection(
                    server_name=server_name,
                    session=session,
                    stack=stack,
                    tools=list(tools_res.tools),
                )
                self._connections[key] = conn
                return conn
            except BaseException:
                await stack.aclose()
                raise

    async def list_apps(self, profile: str | None = None) -> list[dict[str, Any]]:
        server_configs = self._get_server_configs(profile)
        apps: list[dict[str, Any]] = []
        for server_name in server_configs:
            try:
                conn = await self.get_connection(profile, server_name)
                async with conn.lock:
                    tools_res = await asyncio.wait_for(conn.session.list_tools(), timeout=self.timeout)
                    conn.tools = list(tools_res.tools)
                    for tool in conn.tools:
                        ui = tool_ui(tool)
                        if not ui.get("resourceUri"):
                            continue
                        annotations = getattr(tool, "annotations", None)
                        title = getattr(tool, "title", None) or getattr(annotations, "title", None) or tool.name
                        icons = getattr(tool, "icons", None) or []
                        if not isinstance(icons, list):
                            icons = []
                        reg_name = f"mcp__{server_name}__{tool.name}" if model_visible(tool) else None
                        apps.append(
                            {
                                "server": server_name,
                                "tool": tool.name,
                                "title": str(title),
                                "description": str(tool.description or ""),
                                "registry_name": reg_name,
                                "icons": [_jsonable(i) for i in icons],
                                **ui,
                            }
                        )
            except Exception:
                log.warning("Failed to connect or list tools for MCP server '%s'", server_name, exc_info=True)
                continue
        return apps

    async def read_ui(self, profile: str | None, server_name: str, uri: str) -> dict[str, Any]:
        if not isinstance(uri, str) or not uri.startswith(UI_SCHEME):
            raise ValueError("only ui:// resources can be rendered as apps")
        conn = await self.get_connection(profile, server_name)
        async with conn.lock:
            result = await asyncio.wait_for(conn.session.read_resource(uri), timeout=self.timeout)
            contents = getattr(result, "contents", None) or []
            for item in contents:
                text = getattr(item, "text", None)
                if text is None and getattr(item, "blob", None) is not None:
                    try:
                        text = base64.b64decode(item.blob).decode("utf-8", errors="replace")
                    except Exception:
                        text = None
                if text is None:
                    continue
                encoded = text.encode("utf-8")
                if len(encoded) > MAX_UI_BYTES:
                    raise ValueError(f"app document exceeds {MAX_UI_BYTES} bytes")
                meta = getattr(item, "meta", None) or getattr(item, "_meta", None)
                mime = getattr(item, "mimeType", None) or getattr(item, "mime_type", None) or ""
                return {
                    "uri": str(getattr(item, "uri", uri)),
                    "mimeType": str(mime),
                    "html": text,
                    "meta": meta if isinstance(meta, dict) else {},
                }
            raise LookupError(f"'{uri}' returned no document")

    async def call_app_tool(
        self,
        profile: str | None,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conn = await self.get_connection(profile, server_name)
        async with conn.lock:
            tool = next((t for t in conn.tools if t.name == tool_name), None)
            if tool is None:
                tools_res = await asyncio.wait_for(conn.session.list_tools(), timeout=self.timeout)
                conn.tools = list(tools_res.tools)
                tool = next((t for t in conn.tools if t.name == tool_name), None)
            if tool is None:
                raise LookupError(f"tool '{tool_name}' is not on MCP server '{server_name}'")
            ui = tool_ui(tool)
            if "app" not in (ui.get("visibility") or DEFAULT_VISIBILITY):
                raise PermissionError(f"tool '{tool_name}' is not callable from its app (visibility excludes 'app')")

            args = arguments if isinstance(arguments, dict) else {}
            res = await asyncio.wait_for(conn.session.call_tool(tool_name, args), timeout=self.timeout)
            payload = _jsonable(res)
            if len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_APP_RESULT_CHARS:
                raise ValueError("tool result too large for an app")
            return payload

    async def close_all(self) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            for conn in connections:
                try:
                    await conn.stack.aclose()
                except Exception:
                    log.exception("Error closing MCP connection for server '%s'", conn.server_name)
