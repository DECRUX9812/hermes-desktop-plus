"""Repositories for the bridge's spooled records: tool calls, delegation reports, browser thumbnails,
and the spool-offset bookkeeping the ingester uses to stay idempotent."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hermes_plus.store.db import Store

_COMPLETED_STATUSES = ("completed", "success")
_FAILED_STATUSES = ("failed", "error", "timeout", "rejected")
_SUMMARY_MAX_CHARS = 160


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def report_outcome(entries: list[dict[str, Any]]) -> str:
    """The fork's _report_outcome bucketing over per-task result entries."""
    statuses = [str(entry.get("status") or "") for entry in entries]
    truncated = any(entry.get("truncated") or entry.get("exit_reason") == "max_iterations"
                    for entry in entries)
    if statuses and all(s in _COMPLETED_STATUSES for s in statuses) and not truncated:
        return "done"
    completed = sum(1 for s in statuses if s in _COMPLETED_STATUSES)
    failed = sum(1 for s in statuses if s in _FAILED_STATUSES)
    if completed == 0 and failed > 0:
        return "failed"
    return "needs_decision"


def heuristic_summary(entries: list[dict[str, Any]]) -> str:
    for entry in entries:
        for key in ("summary", "error", "output"):
            text = entry.get(key)
            if isinstance(text, str) and text.strip():
                return " ".join(text.strip().split())[:_SUMMARY_MAX_CHARS]
    return ""


class ToolCalls:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record(self, *, tool_call_id: str, session_id: str, tool_name: str, status: str,
               error_type: str | None, error_message: str | None, args: Any,
               result: Any, duration_ms: int | None, created_at: float,
               detail: dict[str, Any] | None = None) -> None:
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO tool_calls (tool_call_id, session_id, tool_name, status, error_type, "
                "error_message, args_json, result_excerpt, detail_json, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tool_call_id) DO UPDATE SET "
                "session_id = excluded.session_id, tool_name = excluded.tool_name, "
                "status = excluded.status, error_type = excluded.error_type, "
                "error_message = excluded.error_message, args_json = excluded.args_json, "
                "result_excerpt = excluded.result_excerpt, detail_json = excluded.detail_json, "
                "duration_ms = excluded.duration_ms",
                (tool_call_id, session_id, tool_name, status, error_type, error_message,
                 _json(args), _json(result), _json(detail), duration_ms, created_at))

    def list_for_session(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY created_at DESC, rowid DESC "
                "LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def record_detail(self, tool_call_id: str, detail: dict[str, Any]) -> None:
        """Attach the bridge's normalized step detail to an already-recorded tool call."""
        with self._store.write() as conn:
            conn.execute("UPDATE tool_calls SET detail_json = ? WHERE tool_call_id = ?",
                         (_json(detail), tool_call_id))


def _json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


class DelegationReports:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record_from_spool(self, *, delegation_id: str, session_id: str, state: str,
                          entries: list[dict[str, Any]], child_session_ids: dict[str, str],
                          group: str | None, duration_seconds: float | None,
                          completed_at: float) -> None:
        outcome = report_outcome(entries)
        completed = sum(1 for e in entries if str(e.get("status") or "") in _COMPLETED_STATUSES)
        failed = sum(1 for e in entries if str(e.get("status") or "") in _FAILED_STATUSES)
        title = " ".join(str(group or "Delegation").split())[:200]
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO delegation_reports (delegation_id, session_id, state, outcome, title, "
                "summary, summary_source, task_count, completed_count, failed_count, "
                "duration_seconds, completed_at, delivery_state, child_session_ids_json, group_name) "
                "VALUES (?, ?, ?, ?, ?, ?, 'heuristic', ?, ?, ?, ?, ?, '', ?, ?) "
                "ON CONFLICT(delegation_id) DO UPDATE SET "
                "state = excluded.state, outcome = excluded.outcome, summary = excluded.summary, "
                "task_count = excluded.task_count, completed_count = excluded.completed_count, "
                "failed_count = excluded.failed_count, duration_seconds = excluded.duration_seconds, "
                "completed_at = excluded.completed_at, child_session_ids_json = excluded.child_session_ids_json",
                (delegation_id, session_id, state, outcome, title,
                 heuristic_summary(entries), len(entries), completed, failed,
                 duration_seconds, completed_at, _json(child_session_ids) or "{}", group))

    def list_for_session(self, session_id: str, limit: int = 25) -> list[dict[str, Any]]:
        """The fork's tui_gateway/contracts/delegation_reports.py shape."""
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM delegation_reports WHERE session_id = ? AND delivery_state != 'dropped' "
                "AND state NOT IN ('running', 'stalling', 'finalizing') "
                "ORDER BY completed_at DESC LIMIT ?", (session_id, limit)).fetchall()
        reports = []
        for row in rows:
            record = dict(row)
            try:
                child_ids = json.loads(record.pop("child_session_ids_json") or "{}")
            except ValueError:
                child_ids = {}
            reports.append({
                "delegation_id": record["delegation_id"],
                "state": record["state"],
                "outcome": record["outcome"],
                "title": record["title"],
                "summary": record["summary"],
                "summary_source": record["summary_source"],
                "task_count": record["task_count"],
                "completed_count": record["completed_count"],
                "failed_count": record["failed_count"],
                "duration_seconds": record["duration_seconds"],
                "completed_at": record["completed_at"],
                "delivery_state": record["delivery_state"],
                "child_session_ids": child_ids if isinstance(child_ids, dict) else {},
                "group": record["group_name"],
            })
        return reports


class BrowserThumbnails:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record(self, *, tool_call_id: str, session_id: str, tool_name: str, url: str | None,
               title: str | None, captured_at: float | None, image_path: str | None,
               snapshot: str | None) -> None:
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO browser_thumbnails (tool_call_id, session_id, tool_name, url, title, "
                "captured_at, image_path, snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tool_call_id) DO UPDATE SET "
                "session_id = excluded.session_id, tool_name = excluded.tool_name, url = excluded.url, "
                "title = excluded.title, captured_at = excluded.captured_at, "
                "image_path = excluded.image_path, snapshot = excluded.snapshot",
                (tool_call_id, session_id, tool_name, url, title, captured_at, image_path, snapshot))

    def get(self, tool_call_id: str) -> dict[str, Any] | None:
        with self._store.read() as conn:
            row = conn.execute(
                "SELECT * FROM browser_thumbnails WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return _row_dict(row)


class SpoolOffsets:
    def __init__(self, store: Store) -> None:
        self._store = store

    def get(self, filename: str) -> int:
        with self._store.read() as conn:
            row = conn.execute(
                "SELECT offset FROM spool_offsets WHERE filename = ?", (filename,)).fetchone()
        return int(row["offset"]) if row is not None else 0

    def set(self, filename: str, offset: int) -> None:
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO spool_offsets (filename, offset, ingested_at) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO UPDATE SET offset = excluded.offset, "
                "ingested_at = excluded.ingested_at",
                (filename, offset, time.time()))

    def forget(self, filename: str) -> None:
        with self._store.write() as conn:
            conn.execute("DELETE FROM spool_offsets WHERE filename = ?", (filename,))
