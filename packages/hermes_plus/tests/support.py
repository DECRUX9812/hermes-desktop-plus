from __future__ import annotations

from typing import Any


def ok(reply: dict[str, Any]) -> Any:
    assert "error" not in reply, reply
    return reply["result"]


def fault(reply: dict[str, Any]) -> dict[str, Any]:
    assert "result" not in reply, reply
    return reply["error"]
