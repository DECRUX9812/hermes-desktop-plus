"""Per-profile memory notes (MEMORY and USER targets) with the fork's char budgets and entry ids."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from hermes_plus.store.db import Store

TARGETS = ("memory", "user")
CHAR_LIMITS = {"memory": 2200, "user": 1375}
ENTRY_DELIMITER = "\n§\n"


class MemoryRejected(Exception):
    """The write was refused: empty text, duplicate on replace, or over the char budget."""


class MemoryItemChanged(Exception):
    """The entry id no longer exists for this profile."""


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    target: str
    text: str


def entry_id(target: str, text: str) -> str:
    return hashlib.sha1(f"{target}\0{text}".encode()).hexdigest()[:12]


def _check_target(target: str) -> None:
    if target not in TARGETS:
        raise MemoryRejected(f"Invalid memory target '{target}'. Use 'memory' or 'user'.")


class MemoryNotes:
    def __init__(self, store: Store) -> None:
        self._store = store

    def _texts(self, conn: sqlite3.Connection, profile: str, target: str) -> list[str]:
        rows = conn.execute(
            "SELECT text FROM memory_notes WHERE profile = ? AND target = ? ORDER BY created_at, rowid",
            (profile, target)).fetchall()
        return [row["text"] for row in rows]

    def snapshot(self, profile: str) -> dict[str, Any]:
        with self._store.read() as conn:
            entries = []
            usage = {}
            for target in TARGETS:
                rows = conn.execute(
                    "SELECT id, text FROM memory_notes WHERE profile = ? AND target = ? "
                    "ORDER BY created_at, rowid", (profile, target)).fetchall()
                entries.extend(MemoryEntry(row["id"], target, row["text"]) for row in rows)
                used = len(ENTRY_DELIMITER.join(row["text"] for row in rows))
                usage[target] = {"used": used, "limit": CHAR_LIMITS[target]}
        return {"entries": entries, "usage": usage}

    def add(self, profile: str, target: str, content: str) -> None:
        _check_target(target)
        text = content.strip()
        if not text:
            raise MemoryRejected("Content cannot be empty.")
        with self._store.write() as conn:
            texts = self._texts(conn, profile, target)
            if text in texts:
                return
            self._check_budget(target, texts + [text])
            conn.execute(
                "INSERT INTO memory_notes (profile, id, target, text, created_at) VALUES (?, ?, ?, ?, ?)",
                (profile, entry_id(target, text), target, text, time.time()))

    def replace(self, profile: str, item_id: str, content: str) -> None:
        text = content.strip()
        if not text:
            raise MemoryRejected("new_content cannot be empty. Use 'remove' to delete entries.")
        with self._store.write() as conn:
            current = self._find(conn, profile, item_id)
            target = current["target"]
            texts = [t for t in self._texts(conn, profile, target) if t != current["text"]]
            if text in texts:
                raise MemoryRejected("Entry already exists.")
            self._check_budget(target, texts + [text])
            conn.execute("DELETE FROM memory_notes WHERE profile = ? AND id = ?", (profile, item_id))
            conn.execute(
                "INSERT INTO memory_notes (profile, id, target, text, created_at) VALUES (?, ?, ?, ?, ?)",
                (profile, entry_id(target, text), target, text, current["created_at"]))

    def remove(self, profile: str, item_id: str) -> None:
        with self._store.write() as conn:
            self._find(conn, profile, item_id)
            conn.execute("DELETE FROM memory_notes WHERE profile = ? AND id = ?", (profile, item_id))

    def _find(self, conn: sqlite3.Connection, profile: str, item_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id, target, text, created_at FROM memory_notes WHERE profile = ? AND id = ?",
            (profile, item_id)).fetchone()
        if row is None:
            raise MemoryItemChanged(item_id)
        return row

    @staticmethod
    def _check_budget(target: str, texts: list[str]) -> None:
        total = len(ENTRY_DELIMITER.join(texts))
        limit = CHAR_LIMITS[target]
        if total > limit:
            raise MemoryRejected(
                f"Memory at {total:,}/{limit:,} chars would exceed the limit by {total - limit:,} chars.")
