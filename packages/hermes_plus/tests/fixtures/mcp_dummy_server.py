"""Stdio MCP server fixture for tests."""

import sys
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test_fixture")


@mcp.tool(
    name="calc_add",
    title="Calculator Add",
    description="Adds two numbers",
    meta={"ui": {"resourceUri": "ui://calc/view", "visibility": ["model", "app"]}},
)
def calc_add(a: int, b: int) -> int:
    return a + b


@mcp.tool(
    name="model_only_tool",
    title="Model Only Tool",
    description="Tool not visible to apps",
    meta={"ui": {"visibility": ["model"]}},
)
def model_only_tool() -> str:
    return "forbidden for apps"


@mcp.resource("ui://calc/view", mime_type="text/html;profile=mcp-app", meta={"csp": "default-src 'self'"})
def calc_view() -> str:
    return "<h1>Calculator App</h1>"


if __name__ == "__main__":
    mcp.run()
