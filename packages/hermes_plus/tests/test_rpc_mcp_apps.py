"""End-to-end tests for mcp_apps.* RPC methods with real stdio MCP server."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_plus.rpc import RpcContext, handle_text
from hermes_plus.rpc.errors import MCP_APP_PARAM_OR_LOOKUP, MCP_APP_PERMISSION
from hermes_plus.store import Store
from support import fault, ok


@pytest.fixture
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "hermes-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    server_script = Path(__file__).parent / "fixtures" / "mcp_dummy_server.py"

    config = {
        "mcp_servers": {
            "demo_srv": {
                "command": sys.executable,
                "args": [str(server_script)],
            }
        }
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return home


@pytest.mark.asyncio
async def test_mcp_apps_list_and_read_ui_and_call(store: Store, mcp_env: Path):
    ctx = RpcContext(store=store)
    try:
        # 1. Test mcp_apps.list
        res_list = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 1, "method": "mcp_apps.list", "params": {}}',
        )
        assert res_list is not None
        import json

        data = ok(json.loads(res_list))
        apps = data.get("apps", [])
        assert len(apps) == 1
        app = apps[0]
        assert app["server"] == "demo_srv"
        assert app["tool"] == "calc_add"
        assert app["title"] == "Calculator Add"
        assert app["description"] == "Adds two numbers"
        assert app["registry_name"] == "mcp__demo_srv__calc_add"
        assert app["resourceUri"] == "ui://calc/view"
        assert "app" in app["visibility"]
        assert "model" in app["visibility"]

        # 2. Test mcp_apps.read_ui
        res_ui = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 2, "method": "mcp_apps.read_ui", "params": {"server": "demo_srv", "uri": "ui://calc/view"}}',
        )
        assert res_ui is not None
        ui_data = ok(json.loads(res_ui))
        assert ui_data["uri"] == "ui://calc/view"
        assert "<h1>Calculator App</h1>" in ui_data["html"]
        assert "text/html" in ui_data["mimeType"]

        # 3. Test mcp_apps.call (valid)
        res_call = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 3, "method": "mcp_apps.call", "params": {"server": "demo_srv", "tool": "calc_add", "arguments": {"a": 15, "b": 27}}}',
        )
        assert res_call is not None
        call_data = ok(json.loads(res_call))
        assert call_data.get("isError") is False
        content = call_data.get("content", [])
        assert len(content) > 0
        assert content[0].get("text") == "42"

        # 4. Test mcp_apps.call (forbidden - app visibility excluded)
        res_forbidden = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 4, "method": "mcp_apps.call", "params": {"server": "demo_srv", "tool": "model_only_tool"}}',
        )
        assert res_forbidden is not None
        err = fault(json.loads(res_forbidden))
        assert err["code"] == MCP_APP_PERMISSION
        assert "not callable from its app" in err["message"]

        # 5. Test mcp_apps.read_ui with non-ui scheme
        res_bad_scheme = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 5, "method": "mcp_apps.read_ui", "params": {"server": "demo_srv", "uri": "http://example.com"}}',
        )
        assert res_bad_scheme is not None
        err_scheme = fault(json.loads(res_bad_scheme))
        assert err_scheme["code"] == MCP_APP_PARAM_OR_LOOKUP
        assert "only ui:// resources" in err_scheme["message"]

        # 6. Test mcp_apps.call with unknown tool
        res_unknown_tool = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 6, "method": "mcp_apps.call", "params": {"server": "demo_srv", "tool": "nonexistent"}}',
        )
        assert res_unknown_tool is not None
        err_tool = fault(json.loads(res_unknown_tool))
        assert err_tool["code"] == MCP_APP_PARAM_OR_LOOKUP
        assert "not on MCP server" in err_tool["message"]

        # 7. Test missing params
        res_missing = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 7, "method": "mcp_apps.read_ui", "params": {}}',
        )
        assert res_missing is not None
        err_missing = fault(json.loads(res_missing))
        assert err_missing["code"] == MCP_APP_PARAM_OR_LOOKUP
        assert "server required" in err_missing["message"]

        # 8. Test unknown server
        res_unknown_srv = await handle_text(
            ctx,
            '{"jsonrpc": "2.0", "id": 8, "method": "mcp_apps.read_ui", "params": {"server": "ghost", "uri": "ui://calc/view"}}',
        )
        assert res_unknown_srv is not None
        err_srv = fault(json.loads(res_unknown_srv))
        assert err_srv["code"] == MCP_APP_PARAM_OR_LOOKUP
        assert "not configured" in err_srv["message"]
    finally:
        await ctx.mcp_manager.close_all()
