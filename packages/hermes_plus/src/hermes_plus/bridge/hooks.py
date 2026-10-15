"""Hook observers: record tool calls, delegation completions, browser steps and approvals to the spool.

Every callback is fail-open: it appends bounded JSON to the spool and never raises into the agent.
These are the fork's core patches (hermes_state_tool_retries, async_delegation reports,
browser_step_thumbnail, computer timeline) re-expressed as a plugin's ``post_tool_call`` records;
the sidecar ingests them into plus.db.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import paths
from . import spool as spool_mod

log = logging.getLogger(__name__)

# The fork's tools/browser_step_thumbnail.py set; a post_tool_call on one of these carries the
# page url/title and often a screenshot path we can copy.
BROWSER_STEP_TOOLS = frozenset({
    "browser_navigate", "browser_click", "browser_type", "browser_scroll",
    "browser_back", "browser_press", "browser_snapshot", "browser_vision",
})
COMPUTER_PREFIXES = ("computer", "cua")
DELEGATE_TOOL = "delegate_task"

_ARGS_LIMIT = 4000
_RESULT_LIMIT = 8000
_SNAPSHOT_LIMIT = 16 * 1024

_spool: spool_mod.Spool | None = None


def _get_spool() -> spool_mod.Spool:
    global _spool
    if _spool is None:
        _spool = spool_mod.get_spool(paths.plus_home(None))
    return _spool


def _session_id(kwargs: dict[str, Any]) -> str:
    for key in ("session_id", "session_key", "gateway_session_id"):
        value = kwargs.get(key)
        if value:
            return str(value)
    return ""


def _parsed_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def register_hooks(register: Any) -> list[str]:
    """Register every hook through the guarded ``register_hook``; returns the features that landed."""
    registered: list[str] = []
    for name, callback in (
        ("post_tool_call", post_tool_call),
        ("pre_approval_request", pre_approval_request),
        ("post_approval_response", post_approval_response),
    ):
        try:
            register(name, callback)
        except Exception:
            log.debug("hook %s is not available on this Hermes", name)
        else:
            registered.append(f"hook.{name}")
    return registered


def post_tool_call(**kwargs: Any) -> None:
    """Observer: one spool record per finished tool call, plus feature-specific envelopes."""
    try:
        _post_tool_call(kwargs)
    except Exception:
        log.exception("hermes-plus post_tool_call observer failed")


def _post_tool_call(kwargs: dict[str, Any]) -> None:
    tool_name = str(kwargs.get("tool_name") or "")
    tool_call_id = str(kwargs.get("tool_call_id") or "")
    session_id = _session_id(kwargs)
    result = kwargs.get("result")
    payload = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "task_id": str(kwargs.get("task_id") or ""),
        "args": spool_mod.truncate(kwargs.get("args"), _ARGS_LIMIT),
        "result": spool_mod.truncate(result, _RESULT_LIMIT),
        "duration_ms": kwargs.get("duration_ms"),
        "status": str(kwargs.get("status") or ""),
        "error_type": kwargs.get("error_type"),
        "error_message": spool_mod.truncate(kwargs.get("error_message"), 1000),
    }
    _get_spool().append("tool_call", session_id, payload)

    parsed = _parsed_result(result)
    if tool_name == DELEGATE_TOOL:
        _record_delegation(session_id, tool_call_id, parsed, kwargs)
    if tool_name in BROWSER_STEP_TOOLS:
        _record_browser_step(session_id, tool_call_id, tool_name, parsed, kwargs)
    if tool_name.startswith(COMPUTER_PREFIXES):
        _record_computer_step(session_id, tool_call_id, tool_name, parsed, payload)


def _record_delegation(session_id: str, tool_call_id: str, parsed: dict[str, Any], kwargs: Any) -> None:
    """The fork's delegate_tool_dispatch combined result: report fields ride the tool result."""
    entries = parsed.get("results")
    if not isinstance(entries, list) or not entries:
        return
    payload = {
        "delegation_id": str(parsed.get("delegation_id") or tool_call_id),
        "state": str(parsed.get("state") or "completed"),
        "results": entries,
        "child_session_ids": parsed.get("child_session_ids") or {},
        "group": parsed.get("group"),
        "total_duration_seconds": parsed.get("total_duration_seconds"),
        "duration_seconds": kwargs.get("duration_ms"),
    }
    _get_spool().append("delegation", session_id, payload)


def _record_browser_step(session_id: str, tool_call_id: str, tool_name: str,
                         parsed: dict[str, Any], kwargs: Any) -> None:
    url = _first(parsed, ("url", "current_url", "page_url"))
    title = _first(parsed, ("page_title", "title"))
    screenshot = _first(parsed, ("screenshot_path", "screenshot", "image_path"))
    if not url and not title and not screenshot:
        return
    payload = {
        "tool_call_id": tool_call_id or f"{tool_name}-{int(kwargs.get('duration_ms') or 0)}",
        "tool_name": tool_name,
        "url": url,
        "title": title,
        "screenshot_path": screenshot,
        "captured_at": None,
        "snapshot": spool_mod.truncate(parsed.get("snapshot"), _SNAPSHOT_LIMIT)
        if isinstance(parsed.get("snapshot"), str) else None,
    }
    _get_spool().append("browser_thumbnail", session_id, payload)


def _record_computer_step(session_id: str, tool_call_id: str, tool_name: str,
                          parsed: dict[str, Any], payload: dict[str, Any]) -> None:
    detail = {
        "command": _first(parsed, ("command", "cmd", "script", "code")),
        "path": _first(parsed, ("path", "file_path", "filename", "target")),
        "url": _first(parsed, ("url", "current_url", "page_url")),
        "page_title": _first(parsed, ("page_title", "title")),
        "screenshot_path": _first(parsed, ("screenshot_path", "screenshot", "image_path")),
    }
    _get_spool().append("computer_step", session_id, {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": payload["status"],
        "error_message": payload["error_message"],
        "detail": {key: value for key, value in detail.items() if value},
    })


def pre_approval_request(**kwargs: Any) -> None:
    """Observer: an approval prompt was raised (the journal request half)."""
    try:
        _get_spool().append("approval_request", _session_id(kwargs), {
            "command": spool_mod.truncate(kwargs.get("command"), 2000),
            "description": spool_mod.truncate(kwargs.get("description"), 2000),
            "pattern_key": kwargs.get("pattern_key"),
            "session_key": kwargs.get("session_key"),
            "surface": kwargs.get("surface"),
            "tool_call_id": kwargs.get("tool_call_id"),
            "turn_id": kwargs.get("turn_id"),
        })
    except Exception:
        log.exception("hermes-plus pre_approval_request observer failed")


def post_approval_response(**kwargs: Any) -> None:
    """Observer: the user answered an approval prompt (the journal decision half)."""
    try:
        _get_spool().append("approval_decision", _session_id(kwargs), {
            "command": spool_mod.truncate(kwargs.get("command"), 2000),
            "description": spool_mod.truncate(kwargs.get("description"), 2000),
            "session_key": kwargs.get("session_key"),
            "choice": kwargs.get("choice"),
            "decided_by": kwargs.get("decided_by"),
            "tool_call_id": kwargs.get("tool_call_id"),
            "turn_id": kwargs.get("turn_id"),
        })
    except Exception:
        log.exception("hermes-plus post_approval_response observer failed")
