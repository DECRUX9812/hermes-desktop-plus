"""The home-feed card model's state: dismissed cards and pinned cards.

The fork kept dismissed feed ids in the desktop's localStorage
(``hermes.desktop.home-feed.dismissed.v1``); a plugin has no renderer to hold
that, so the state lives in the plugin's own sqlite file — the same
plugin-owned-store rule the command-room and group-rooms plugins follow.
Nothing here touches core Hermes or the add-on's plus.db schema.

This module is deliberately config-free: knobs are read at call time by the
plugin layer in ``__init__.py`` through the captured plugin ctx and passed in
as plain arguments, so the store stays testable and host-agnostic.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dismissed (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pins (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
"""


class FeedError(Exception):
    """Caller-visible feed-state failure (empty id, unknown pin, ...)."""


class CardNotFound(FeedError):
    pass


def _now() -> int:
    return int(time.time())


class FeedStore:
    """One sqlite file of feed state; safe to share across threads, not across processes."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── dismissed cards ──────────────────────────────────────────────────────────────────

    def dismiss(self, card_id: str) -> dict[str, Any]:
        card_id = str(card_id or "").strip()
        if not card_id:
            raise FeedError("card id must not be empty")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO dismissed (id, created_at) VALUES (?, ?) "
                "ON CONFLICT (id) DO NOTHING", (card_id, _now()))
        return {"id": card_id, "dismissed": True}

    def restore(self, card_id: str) -> bool:
        """Un-dismiss; False when the id was not dismissed."""
        card_id = str(card_id or "").strip()
        if not card_id:
            raise FeedError("card id must not be empty")
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM dismissed WHERE id = ?", (card_id,))
            return cursor.rowcount > 0

    def dismissed_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM dismissed ORDER BY created_at, id").fetchall()
        return [str(row["id"]) for row in rows]

    # ── pinned cards ─────────────────────────────────────────────────────────────────────

    def pin_add(self, card_id: str, *, title: str = "") -> dict[str, Any]:
        card_id = str(card_id or "").strip()
        if not card_id:
            raise FeedError("card id must not be empty")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pins (id, title, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET title = excluded.title",
                (card_id, str(title or ""), _now()))
        return {"id": card_id, "title": str(title or ""), "pinned": True}

    def pin_remove(self, card_id: str) -> bool:
        card_id = str(card_id or "").strip()
        if not card_id:
            raise FeedError("card id must not be empty")
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM pins WHERE id = ?", (card_id,))
            return cursor.rowcount > 0

    def pins(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at FROM pins ORDER BY created_at DESC, id").fetchall()
        return [{"id": row["id"], "title": row["title"], "created_at": row["created_at"]} for row in rows]

    def pinned_ids(self) -> list[str]:
        return [pin["id"] for pin in self.pins()]
