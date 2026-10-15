"""Approval journal: one row per gateway approval request, decided at most once."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from hermes_plus.store.db import Store

PENDING = "pending"
DECISION_CHOICES = frozenset({"once", "session", "always", "deny"})
_GRANT_CHOICES = frozenset({"once", "session", "always"})

_COLUMNS = ("request_id, session_id, tool_call_id, tool_name, command, description, created_at, status, "
            "decided_at, choice, ran_at, reason")


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class ApprovalJournal:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record_request(self, *, request_id: str, session_id: str, tool_call_id: str,
                       tool_name: str, command: str, description: str) -> dict[str, Any]:
        with self._store.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO approvals (request_id, session_id, tool_call_id, tool_name, command, "
                "description, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, session_id, tool_call_id, tool_name, command, description, time.time(), PENDING))
            row = conn.execute(f"SELECT {_COLUMNS} FROM approvals WHERE request_id = ?", (request_id,)).fetchone()
        return dict(row)

    def decide(self, request_id: str, choice: str) -> dict[str, Any] | None:
        """Record the first decision on a pending request; later calls return the row unchanged."""
        status = "allowed" if choice in _GRANT_CHOICES else "denied"
        with self._store.write() as conn:
            conn.execute(
                "UPDATE approvals SET status = ?, choice = ?, decided_at = ? WHERE request_id = ? AND status = ?",
                (status, choice, time.time(), request_id, PENDING))
            row = conn.execute(f"SELECT {_COLUMNS} FROM approvals WHERE request_id = ?", (request_id,)).fetchone()
        return _row_dict(row)

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._store.read() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM approvals WHERE request_id = ?", (request_id,)).fetchone()
        return _row_dict(row)

    def list_for_session(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._store.read() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM approvals WHERE session_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]
