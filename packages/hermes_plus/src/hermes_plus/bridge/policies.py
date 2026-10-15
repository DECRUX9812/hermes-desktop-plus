"""The fork's retry-loop policy, re-expressed as a public-API plugin hook.

``agent/policies.py`` (fork) stopped a turn between iterations when the model kept calling the same
tool with the same arguments — any one ``(name, args)`` signature appearing ``retry_loop_max_identical``
times inside the trailing ``2 * max_identical`` tool calls. A plugin cannot end the loop, but it can
refuse the repeat through ``pre_tool_call``: the model gets the same diagnostic and has to change
approach. The detector runs on the fork's defaults (on, 6) and is configurable through this plugin's
own settings subtree, ``plugins.entries.hermes-plus.settings.{retry_loop_detector,
retry_loop_max_identical}`` — a plugin cannot read the fork's ``agent.policies`` keys.

The cost cap half of the fork's policies (``max_cost_usd``) is not portable: no public hook can end
a turn, so the bridge does not pretend to enforce it (see REPORT.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import deque
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MAX_IDENTICAL = 6
_ARGS_MAX_CHARS = 4096
_MAX_TRACKED_TURNS = 64
_BLOCK_MESSAGE = (
    "Same tool call repeated {limit}+ times ({tool}) — stopping a stuck retry loop. "
    "Change approach or ask the user instead of repeating this call."
)


def _signature(tool_name: str, args: Any) -> str:
    """``name:sha256(canonical args)[:16]`` — the fork's ``_tool_call_signature``."""
    if isinstance(args, str):
        canonical = args[:_ARGS_MAX_CHARS]
    else:
        try:
            canonical = json.dumps(args, sort_keys=True, default=str)[:_ARGS_MAX_CHARS]
        except (TypeError, ValueError):
            canonical = str(args)[:_ARGS_MAX_CHARS]
    digest = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{tool_name}:{digest}"


class RetryLoopDetector:
    """Per-turn window of tool-call signatures; trips when one signature repeats ``limit`` times."""

    def __init__(self, limit: int = DEFAULT_MAX_IDENTICAL) -> None:
        self.limit = max(1, int(limit))
        self._windows: dict[tuple[str, str], deque[str]] = {}
        self._lock = threading.Lock()

    def observe(self, *, tool_name: str, args: Any, session_id: str, turn_id: str) -> str | None:
        """Record one call; returns the block message when the turn is stuck, else None.

        Calls already made this turn are what counts (the fork trips between iterations), so the
        call being dispatched is measured against the window it joins, not added to it first.
        """
        if not turn_id:
            return None  # no turn identity: fail open rather than count across turns
        signature = _signature(tool_name, args)
        key = (session_id, turn_id)
        window_size = 2 * self.limit
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                window = deque(maxlen=window_size)
                self._windows[key] = window
                if len(self._windows) > _MAX_TRACKED_TURNS:
                    self._windows.pop(next(iter(self._windows)))
            stuck = list(window).count(signature) >= self.limit
            window.append(signature)
        if not stuck:
            return None
        return _BLOCK_MESSAGE.format(limit=self.limit, tool=tool_name or "tool")


def register_policy_hooks(ctx: Any, register: Any) -> list[str]:
    """Register ``pre_tool_call`` when the detector is enabled; returns the features that landed."""
    if not bool(_config(ctx, "retry_loop_detector", True)):
        return []
    raw = _config(ctx, "retry_loop_max_identical", DEFAULT_MAX_IDENTICAL)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_IDENTICAL
    if limit <= 0:
        return []
    detector = RetryLoopDetector(limit)

    def pre_tool_call(**kwargs: Any) -> dict[str, Any] | None:
        try:
            message = detector.observe(
                tool_name=str(kwargs.get("tool_name") or ""),
                args=kwargs.get("args"),
                session_id=str(kwargs.get("session_id") or ""),
                turn_id=str(kwargs.get("turn_id") or ""),
            )
        except Exception:
            log.exception("hermes-plus retry-loop detector failed; the tool call proceeds")
            return None
        return {"action": "block", "message": message} if message else None

    try:
        register("pre_tool_call", pre_tool_call)
    except Exception:
        log.debug("pre_tool_call is not available on this Hermes")
        return []
    return ["hook.pre_tool_call"]


def _config(ctx: Any, key: str, default: Any) -> Any:
    get_config = getattr(ctx, "get_config", None)
    if get_config is None:
        return default
    try:
        return get_config(key, default)
    except Exception:
        log.debug("could not read plugin setting %s; using %r", key, default)
        return default
