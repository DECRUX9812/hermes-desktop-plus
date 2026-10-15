"""fork.computer.timeline: computer-use steps derived from the stored tool-call records.

The fork derived this read-only from session messages (tui_gateway/methods_fork_computer.py); we
derive it the same way from the bridge's spooled tool_call rows in plus.db, keeping the fork's
contract shape (tui_gateway/contracts/fork_computer.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import PROFILE_REQUIRED, RpcFault
from hermes_plus.rpc.params import read_text, require_text
from hermes_plus.rpc.registry import method

_DIFF_MAX_BYTES = 16 * 1024
_OUTPUT_TAIL_BYTES = 4 * 1024
_TITLE_MAX_CHARS = 120
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 500


@method("fork.computer.timeline")
def timeline(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    if not read_text(params, "profile"):
        raise RpcFault(PROFILE_REQUIRED, "profile required")
    session_id = require_text(params, "session_id", PROFILE_REQUIRED, "session_id required")
    limit = params.get("limit")
    limit = _DEFAULT_LIMIT if not isinstance(limit, int) else max(1, min(limit, _MAX_LIMIT))

    rows = ctx.tool_calls.list_for_session(session_id, limit=_MAX_LIMIT * 2)
    steps = [_step(row, index) for index, row in enumerate(rows[-limit:])]
    return {"session_id": session_id, "steps": steps, "todos": _todos(rows)}


def _step(row: dict[str, Any], index: int) -> dict[str, Any]:
    tool = str(row["tool_name"] or "")
    stored_detail = _parsed(row["detail_json"])
    args = _parsed(row["args_json"])
    result = _parsed(row["result_excerpt"])
    detail = stored_detail or _detail(args, result)
    return {
        "id": str(row["tool_call_id"]),
        "index": index,
        "tool": tool,
        "kind": _kind(tool),
        "title": _title(tool, args, detail),
        "status": _status(row),
        "at": _iso(row["created_at"]),
        "duration_s": round(row["duration_ms"] / 1000, 3) if row["duration_ms"] else None,
        "detail": detail,
    }


def _kind(tool: str) -> str:
    name = tool.lower()
    if name.startswith("browser"):
        return "browser"
    if name in ("terminal", "execute_code", "process_manage", "process") \
            or name.startswith(("terminal_", "process_")):
        return "terminal"
    if name.startswith(("web_", "search_")) or name == "search":
        return "search"
    if name in ("apply_patch", "str_replace_editor", "file_write", "file_edit", "file_patch") \
            or "file" in name:
        return "file"
    return "other"


def _title(tool: str, args: dict[str, Any], detail: dict[str, Any]) -> str:
    if detail["command"]:
        title = f"Ran {detail['command']}"
    elif detail["path"]:
        verb = "Edited" if any(token in tool.lower() for token in ("edit", "patch", "replace")) else "Wrote"
        title = f"{verb} {detail['path']}"
    elif detail["url"]:
        title = f"Opened {detail['url']}"
    else:
        title = f"Used {tool}"
    return title[:_TITLE_MAX_CHARS]


def _status(row: dict[str, Any]) -> str:
    if str(row["status"] or "") == "error":
        return "error"
    result = _parsed(row["result_excerpt"])
    if result.get("success") is False or result.get("ok") is False or result.get("error"):
        return "error"
    text = row["result_excerpt"] if isinstance(row["result_excerpt"], str) else ""
    if text.lower().startswith(("error:", "failed:")):
        return "error"
    if not str(row["status"] or ""):
        return "running"
    return "done"


def _detail(args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    diff = _first((result, args), ("diff", "inline_diff", "patch", "unified_diff"))
    output = result.get("output") if isinstance(result.get("output"), str) else (
        result.get("text") if isinstance(result.get("text"), str) else None)
    detail: dict[str, Any] = {
        "command": _first((args, result), ("command", "cmd", "script", "code")),
        "output_tail": _tail(output, _OUTPUT_TAIL_BYTES),
        "path": _first((args, result), ("path", "file_path", "filename", "target")),
        "diff": _tail(diff, _DIFF_MAX_BYTES),
        "url": _first((args, result), ("url", "current_url", "page_url")),
        "page_title": _first((result, args), ("page_title", "title")),
        "screenshot_path": _first((result, args), ("screenshot_path", "screenshot", "image_path")),
    }
    return {key: value for key, value in detail.items() if value is not None}


def _first(mappings: tuple[dict[str, Any], ...], keys: tuple[str, ...]) -> str | None:
    for mapping in mappings:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _tail(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    data = text.encode("utf-8")
    return data[-limit:].decode("utf-8", errors="replace")


def _parsed(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _todos(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in reversed(rows):
        if str(row["tool_name"] or "").lower() != "todo":
            continue
        result = _parsed(row["result_excerpt"])
        todos = result.get("todos")
        if isinstance(todos, list):
            return [todo for todo in todos if isinstance(todo, dict)
                    and isinstance(todo.get("id"), str) and isinstance(todo.get("content"), str)]
    return []


def _iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
