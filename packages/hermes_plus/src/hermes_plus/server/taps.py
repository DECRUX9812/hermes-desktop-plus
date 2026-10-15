"""Non-blocking observers for gateway events and approval server requests."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hermes_plus.store.approvals import DECISION_CHOICES, ApprovalJournal

log = logging.getLogger(__name__)
Tap = Callable[["TapContext", dict[str, Any]], None]
TAPS: list[Tap] = []
_STOP = object()


@dataclass
class TapContext:
    runtime: Any
    profile: str | None
    approvals: dict[str, str] = field(default_factory=dict)


class TapDispatcher:
    """Queue frames so storage work never delays WebSocket forwarding."""

    def __init__(self, runtime: Any, profile: str | None) -> None:
        self.context = TapContext(runtime, profile)
        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue(maxsize=1024)
        self._worker = asyncio.create_task(self._run(), name="hermes-plus-event-taps")

    def offer(self, frame: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            log.warning("dropping an upstream event because the tap queue is full")

    async def close(self) -> None:
        await self._queue.put(_STOP)
        await self._worker

    async def _run(self) -> None:
        while (frame := await self._queue.get()) is not _STOP:
            for tap in tuple(TAPS):
                try:
                    await asyncio.to_thread(tap, self.context, frame)
                except Exception:
                    log.exception("Hermes Plus event tap %s failed", getattr(tap, "__name__", tap))


def _store(context: TapContext):
    return context.runtime.store_for(context.profile)


def _approvals(context: TapContext) -> ApprovalJournal:
    return ApprovalJournal(_store(context))


def _record_approval(context: TapContext, frame: dict[str, Any]) -> None:
    method = frame.get("method")
    request_id = frame.get("id")
    params = frame.get("params")
    params = params if isinstance(params, dict) else {}

    if method == "approval" and request_id is not None:
        business_id = str(params.get("request_id") or request_id)
        session_id = str(
            params.get("session_id")
            or params.get("gateway_session_id")
            or params.get("session_id_hint")
            or ""
        )
        _approvals(context).record_request(
            request_id=business_id,
            session_id=session_id,
            tool_call_id=str(params.get("tool_call_id") or ""),
            tool_name=str(params.get("tool_name") or ""),
            command=str(params.get("command") or ""),
            description=str(params.get("description") or ""),
        )
        context.approvals[str(request_id)] = business_id
        return

    if request_id is not None and ("result" in frame or "error" in frame):
        business_id = context.approvals.pop(str(request_id), None)
        result = frame.get("result")
        choice = result.get("choice") if isinstance(result, dict) else None
        if business_id and choice in DECISION_CHOICES:
            _approvals(context).decide(business_id, choice)
        return

    if method == "event":
        event_params = frame.get("params")
        if not isinstance(event_params, dict):
            return
        event_name = event_params.get("type")
        payload = event_params.get("payload")
        if not isinstance(event_name, str) or not event_name.startswith("approval."):
            return
        if event_name == "approval.cancelled" and isinstance(payload, dict):
            rid = str(payload.get("request_id") or "")
            if rid:
                with _store(context).write() as conn:
                    conn.execute(
                        "UPDATE approvals SET status = 'withdrawn', reason = ? "
                        "WHERE request_id = ? AND status = 'pending'",
                        (str(payload.get("reason") or "cancelled"), rid),
                    )


def _record_tool_activity(context: TapContext, frame: dict[str, Any]) -> None:
    if frame.get("method") != "event":
        return
    params = frame.get("params")
    if not isinstance(params, dict):
        return
    event_name = params.get("type")
    payload = params.get("payload")
    if event_name not in {"tool.start", "tool.generating", "tool.complete"}:
        return
    if not isinstance(payload, dict):
        return
    session_id = str(params.get("session_id") or payload.get("session_id") or "")
    call_id = str(payload.get("tool_id") or payload.get("tool_call_id") or "")
    if not session_id or not call_id:
        return
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _store(context).write() as conn:
        conn.execute(
            "INSERT INTO tool_activity "
            "(session_id, tool_call_id, event_name, tool_name, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id, tool_call_id, event_name) DO UPDATE SET "
            "tool_name = excluded.tool_name, payload_json = excluded.payload_json, "
            "created_at = excluded.created_at",
            (session_id, call_id, event_name, str(payload.get("name") or ""), encoded, time.time()),
        )


TAPS.extend((_record_approval, _record_tool_activity))


__all__ = ["TAPS", "TapContext", "TapDispatcher"]
