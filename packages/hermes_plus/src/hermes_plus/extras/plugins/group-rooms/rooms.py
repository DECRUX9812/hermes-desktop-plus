"""The group-rooms room model: rooms with bot seats, threads, routing, handoffs, live status.

Ported from the fork's Room 2.0 / group-chat architecture (hermes-bots group-chat-view,
bot-relay handoffs): one composer posts into a room, seats are bot identities, threads
carry the work, routing decides who answers, and a handoff moves an open thread from one
seat to another. Kept in-plugin on its own sqlite file so nothing touches core Hermes or
the add-on's plus.db schema.

This module is deliberately config-free: knobs (seat limits, message caps, ...) are read
at call time by the plugin layer in ``__init__.py`` through the captured plugin ctx and
passed in as plain arguments, so the store stays testable and host-agnostic.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

ROOM_MODES = ("collab", "round_robin", "lead")
SEAT_STATUSES = ("idle", "pending", "working", "done")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    rr_cursor TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS seats (
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    bot TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'idle',
    status_note TEXT NOT NULL DEFAULT '',
    last_posted_at INTEGER,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (room_id, bot)
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    origin_message_id INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    thread_id INTEGER REFERENCES threads(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    routed_to TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    from_bot TEXT NOT NULL,
    to_bot TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    open INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
"""

USER = "user"


class RoomError(Exception):
    """Caller-visible room-model failure (bad mode, disabled rail, oversized post, ...)."""


class RoomNotFound(RoomError):
    pass


class SeatLimitError(RoomError):
    pass


def _now() -> int:
    return int(time.time())


class RoomStore:
    """One sqlite file of rooms; safe to share across threads, not across processes."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── rooms ────────────────────────────────────────────────────────────────────────────

    def create_room(self, name: str, *, mode: str, goal: str = "", topic: str = "") -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise RoomError("room name must not be empty")
        if mode not in ROOM_MODES:
            raise RoomError(f"unknown room mode {mode!r}; expected one of {', '.join(ROOM_MODES)}")
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO rooms (name, mode, goal, topic, created_at) VALUES (?, ?, ?, ?, ?)",
                    (name, mode, goal, topic, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise RoomError(f"room {name!r} already exists") from exc
        return self.room(name)

    def room(self, name: str) -> dict[str, Any]:
        with self._lock:
            room_row = self._room_row(self._conn, name)
        return self._room_dict(room_row)

    def _room_row(self, conn: sqlite3.Connection, room: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM rooms WHERE name = ?", (str(room),)).fetchone()
        if row is None:
            raise RoomNotFound(str(room))
        return row

    def list_rooms(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM seats s WHERE s.room_id = r.id) AS seat_count"
                " FROM rooms r ORDER BY r.id"
            ).fetchall()
        return [self._room_dict(row) for row in rows]

    # ── seats ────────────────────────────────────────────────────────────────────────────

    def add_seat(self, room: str, bot: str, *, role: str = "member", seat_limit: int | None = None) -> dict[str, Any]:
        bot = str(bot or "").strip()
        if not bot or bot == USER:
            raise RoomError(f"seat name must be a non-empty bot identity (not {USER!r})")
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            existing = self._conn.execute(
                "SELECT * FROM seats WHERE room_id = ? AND bot = ?", (room_row["id"], bot)).fetchone()
            if existing is not None:
                # Idempotent: re-adding only updates the role, so UI retries never fork a seat.
                self._conn.execute(
                    "UPDATE seats SET role = ? WHERE room_id = ? AND bot = ?", (role, room_row["id"], bot))
            else:
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM seats WHERE room_id = ?", (room_row["id"],)).fetchone()[0]
                if seat_limit is not None and count >= seat_limit:
                    raise SeatLimitError(
                        f"room {room_row['name']!r} is at its seat limit ({seat_limit})")
                self._conn.execute(
                    "INSERT INTO seats (room_id, bot, role, added_at) VALUES (?, ?, ?, ?)",
                    (room_row["id"], bot, role, _now()),
                )
        return self.seat(room, bot)

    def remove_seat(self, room: str, bot: str) -> bool:
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            cursor = self._conn.execute(
                "DELETE FROM seats WHERE room_id = ? AND bot = ?", (room_row["id"], str(bot)))
            return cursor.rowcount > 0

    def set_seat_status(self, room: str, bot: str, status: str, *, note: str = "") -> dict[str, Any]:
        if status not in SEAT_STATUSES:
            raise RoomError(f"unknown seat status {status!r}; expected one of {', '.join(SEAT_STATUSES)}")
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            cursor = self._conn.execute(
                "UPDATE seats SET status = ?, status_note = ? WHERE room_id = ? AND bot = ?",
                (status, note, room_row["id"], str(bot)),
            )
            if cursor.rowcount == 0:
                raise RoomError(f"bot {bot!r} has no seat in {room_row['name']!r}")
        return self.seat(room, bot)

    def seat(self, room: str, bot: str) -> dict[str, Any]:
        with self._lock:
            room_row = self._room_row(self._conn, room)
            row = self._conn.execute(
                "SELECT * FROM seats WHERE room_id = ? AND bot = ?", (room_row["id"], str(bot))).fetchone()
        if row is None:
            raise RoomError(f"bot {bot!r} has no seat in {room_row['name']!r}")
        return self._seat_dict(row)

    # ── messages and threads ─────────────────────────────────────────────────────────────

    def post_message(self, room: str, sender: str, text: str, *, thread_id: int | None = None,
                     max_chars: int | None = None) -> dict[str, Any]:
        text = str(text)
        if not text.strip():
            raise RoomError("message text must not be empty")
        if max_chars is not None and len(text) > max_chars:
            raise RoomError(f"message exceeds max_message_chars ({max_chars})")
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            sender = str(sender)
            if sender != USER:
                seat_row = self._conn.execute(
                    "SELECT * FROM seats WHERE room_id = ? AND bot = ?",
                    (room_row["id"], sender)).fetchone()
                if seat_row is None:
                    raise RoomError(f"sender {sender!r} has no seat in {room_row['name']!r}")
                # A pending seat that speaks has picked the work up: live status advances.
                if seat_row["status"] == "pending":
                    self._conn.execute(
                        "UPDATE seats SET status = 'working', last_posted_at = ? WHERE room_id = ? AND bot = ?",
                        (_now(), room_row["id"], sender),
                    )
                else:
                    self._conn.execute(
                        "UPDATE seats SET last_posted_at = ? WHERE room_id = ? AND bot = ?",
                        (_now(), room_row["id"], sender),
                    )
            if thread_id is not None:
                self._thread_row(self._conn, room_row, int(thread_id))
            cursor = self._conn.execute(
                "INSERT INTO messages (room_id, thread_id, sender, text, created_at) VALUES (?, ?, ?, ?, ?)",
                (room_row["id"], thread_id, sender, text, _now()),
            )
            message_id = cursor.lastrowid
        return self.message(room, message_id)

    def message(self, room: str, message_id: int) -> dict[str, Any]:
        with self._lock:
            room_row = self._room_row(self._conn, room)
            row = self._message_row(self._conn, room_row, int(message_id))
        if row is None:
            raise RoomError(f"no message {message_id} in {room_row['name']!r}")
        return self._message_dict(row)

    def _message_row(self, conn: sqlite3.Connection, room_row: sqlite3.Row, message_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ? AND room_id = ?",
            (message_id, room_row["id"])).fetchone()
        if row is None:
            raise RoomError(f"no message {message_id} in {room_row['name']!r}")
        return row

    def start_thread(self, room: str, *, title: str = "", origin_message_id: int | None = None) -> dict[str, Any]:
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            if origin_message_id is not None:
                # Lock-free lookup: self.message() would re-enter the non-reentrant lock.
                self._message_row(self._conn, room_row, int(origin_message_id))
            cursor = self._conn.execute(
                "INSERT INTO threads (room_id, title, origin_message_id, created_at) VALUES (?, ?, ?, ?)",
                (room_row["id"], title, origin_message_id, _now()),
            )
            thread_id = cursor.lastrowid
        return self.thread(room, thread_id)

    def thread(self, room: str, thread_id: int) -> dict[str, Any]:
        with self._lock:
            room_row = self._room_row(self._conn, room)
            row = self._thread_row(self._conn, room_row, int(thread_id))
        return self._thread_dict(row)

    def _thread_row(self, conn: sqlite3.Connection, room_row: sqlite3.Row, thread_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM threads WHERE id = ? AND room_id = ?", (thread_id, room_row["id"])).fetchone()
        if row is None:
            raise RoomError(f"no thread {thread_id} in {room_row['name']!r}")
        return row

    # ── routing ──────────────────────────────────────────────────────────────────────────

    def route_message(self, room: str, message_id: int, *, mode: str | None = None) -> list[dict[str, Any]]:
        """Send a posted message to seats per the room's routing mode.

        collab: every other seat answers together; round_robin: the next seat in a
        per-room rotation; lead: the seat holding the 'lead' role. Routed seats go
        'pending' (the live-status signal the UI renders).
        """
        with self._lock:
            room_row = self._room_row(self._conn, room)
            message_row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ? AND room_id = ?",
                (int(message_id), room_row["id"])).fetchone()
            if message_row is None:
                raise RoomError(f"no message {message_id} in {room_row['name']!r}")
            mode = mode or room_row["mode"]
            if mode not in ROOM_MODES:
                raise RoomError(f"unknown routing mode {mode!r}")
            seats = [
                self._seat_dict(row) for row in self._conn.execute(
                    "SELECT * FROM seats WHERE room_id = ? ORDER BY added_at, bot", (room_row["id"],))
            ]
            sender = message_row["sender"]
            targets = self._targets_for_mode(mode, seats, sender, room_row)
            if targets is None:
                raise RoomError(f"room {room_row['name']!r} has no 'lead' seat to route to")
            names = [target["bot"] for target in targets]
            with self._conn:
                self._conn.execute(
                    "UPDATE messages SET routed_to = ? WHERE id = ?", (json.dumps(names), message_row["id"]))
                for name in names:
                    self._conn.execute(
                        "UPDATE seats SET status = 'pending', status_note = ? WHERE room_id = ? AND bot = ?",
                        (f"routed ({mode})", room_row["id"], name),
                    )
        return [self.seat(room, name) for name in names]

    def _targets_for_mode(self, mode: str, seats: list[dict[str, Any]], sender: str,
                          room_row: sqlite3.Row) -> list[dict[str, Any]] | None:
        others = [seat for seat in seats if seat["bot"] != sender]
        if mode == "collab":
            return others
        if mode == "lead":
            leads = [seat for seat in others if seat["role"] == "lead"]
            return leads if leads else None
        # round_robin: continue the room's rotation, skipping the sender.
        if not others:
            return []
        names = [seat["bot"] for seat in others]
        cursor = room_row["rr_cursor"]
        start = names.index(cursor) + 1 if cursor in names else 0
        picked = names[start % len(names)]
        with self._conn:
            self._conn.execute("UPDATE rooms SET rr_cursor = ? WHERE id = ?", (picked, room_row["id"]))
        return [seat for seat in others if seat["bot"] == picked]

    # ── handoffs ─────────────────────────────────────────────────────────────────────────

    def handoff(self, room: str, thread_id: int, from_bot: str, to_bot: str, *, note: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            self._thread_row(self._conn, room_row, int(thread_id))
            from_bot, to_bot = str(from_bot), str(to_bot)
            if to_bot == from_bot:
                raise RoomError("a handoff must move the thread to a different seat")
            held = self._conn.execute(
                "SELECT bot FROM seats WHERE room_id = ? AND bot IN (?, ?)",
                (room_row["id"], from_bot, to_bot)).fetchall()
            seated = {row["bot"] for row in held}
            for party in (from_bot, to_bot):
                if party != USER and party not in seated:
                    raise RoomError(f"bot {party!r} has no seat in {room_row['name']!r}")
            cursor = self._conn.execute(
                "INSERT INTO handoffs (room_id, thread_id, from_bot, to_bot, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (room_row["id"], int(thread_id), from_bot, to_bot, note, _now()),
            )
            handoff_id = cursor.lastrowid
            self._conn.execute(
                "UPDATE seats SET status = 'pending', status_note = ? WHERE room_id = ? AND bot = ?",
                (f"handoff from {from_bot}", room_row["id"], to_bot),
            )
        return self._handoff(room, handoff_id)

    def resolve_handoff(self, room: str, handoff_id: int) -> dict[str, Any]:
        with self._lock, self._conn:
            room_row = self._room_row(self._conn, room)
            cursor = self._conn.execute(
                "UPDATE handoffs SET open = 0 WHERE id = ? AND room_id = ?",
                (int(handoff_id), room_row["id"]),
            )
            if cursor.rowcount == 0:
                raise RoomError(f"no handoff {handoff_id} in {room_row['name']!r}")
        return self._handoff(room, int(handoff_id))

    def _handoff(self, room: str, handoff_id: int) -> dict[str, Any]:
        with self._lock:
            room_row = self._room_row(self._conn, room)
            row = self._conn.execute(
                "SELECT * FROM handoffs WHERE id = ? AND room_id = ?",
                (handoff_id, room_row["id"])).fetchone()
        if row is None:
            raise RoomError(f"no handoff {handoff_id} in {room_row['name']!r}")
        return self._handoff_dict(row)

    # ── state snapshot ───────────────────────────────────────────────────────────────────

    def room_state(self, room: str) -> dict[str, Any]:
        with self._lock:
            room_row = self._room_row(self._conn, room)
            seats = [
                self._seat_dict(row) for row in self._conn.execute(
                    "SELECT * FROM seats WHERE room_id = ? ORDER BY added_at, bot", (room_row["id"],))
            ]
            threads = [
                self._thread_dict(row) for row in self._conn.execute(
                    "SELECT * FROM threads WHERE room_id = ? ORDER BY id", (room_row["id"],))
            ]
            messages = [
                self._message_dict(row) for row in self._conn.execute(
                    "SELECT * FROM messages WHERE room_id = ? ORDER BY id", (room_row["id"],))
            ]
            handoffs = [
                self._handoff_dict(row) for row in self._conn.execute(
                    "SELECT * FROM handoffs WHERE room_id = ? ORDER BY id", (room_row["id"],))
            ]
        return {
            "room": self._room_dict(room_row), "seats": seats, "threads": threads,
            "messages": messages, "handoffs": handoffs,
        }

    # ── row shapes ───────────────────────────────────────────────────────────────────────

    def _room_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out.pop("rr_cursor", None)  # rotation bookkeeping, not part of the wire shape
        return out

    def _seat_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _thread_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _message_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["thread_id"] = out["thread_id"] if out["thread_id"] is not None else None
        out["routed_to"] = json.loads(out["routed_to"] or "[]")
        return out

    def _handoff_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["open"] = bool(out["open"])
        return out
