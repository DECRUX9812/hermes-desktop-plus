"""Install-wide bot mailbox notes: task hand-offs with an append-only upsert and a status lifecycle."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from hermes_plus.store.db import Store

NOTE_ID_PREFIX = "mbx_"
NOTE_STATUSES = ("open", "accepted", "declined", "done")
TITLE_MAX_CHARS = 200
BODY_MAX_CHARS = 16000
PAYLOAD_MAX_BYTES = 8192
_TRANSITIONS = {
    "open": frozenset({"accepted", "declined", "done"}),
    "accepted": frozenset({"done", "declined"}),
    "declined": frozenset(),
    "done": frozenset(),
}


class MailboxNotFound(Exception):
    pass


class MailboxTransitionError(ValueError):
    pass


def new_note_id() -> str:
    return f"{NOTE_ID_PREFIX}{uuid.uuid4().hex[:20]}"


def _normalize_party(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {"kind": "bot"}
    party = {k: str(raw.get(k) or "") for k in ("kind", "profile", "handle", "name", "connection") if raw.get(k)}
    return party or {"kind": str(raw.get("kind") or "bot")}


def _bounded_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = raw if isinstance(raw, dict) else {"text": str(raw)}
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError):
        return None
    return value if len(encoded) <= PAYLOAD_MAX_BYTES else None


def _normalize_handle(handle: str | None) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _to_wire(row: sqlite3.Row) -> dict[str, Any]:
    note: dict[str, Any] = {
        "id": row["id"],
        "kind": row["kind"],
        "to": json.loads(row["to_json"]),
        "sender": json.loads(row["sender_json"]),
        "title": row["title"],
        "body": row["body"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] is not None else None,
        "status": row["status"],
        "reply": row["reply"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if row["room"]:
        note["room"] = row["room"]
    return note


class Mailbox:
    def __init__(self, store: Store) -> None:
        self._store = store

    def append(self, *, to: Any, sender: Any, title: str, body: str = "", kind: str = "task",
               payload: Any = None, note_id: str | None = None, room: str | None = None) -> dict[str, Any]:
        """Insert a note. An existing id is returned unchanged, so a redelivered note keeps its status."""
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("title required")
        nid = str(note_id or "").strip() or new_note_id()
        to_party = _normalize_party(to)
        sender_party = _normalize_party(sender)
        now = int(time.time())
        with self._store.write() as conn:
            existing = conn.execute("SELECT * FROM mailbox_notes WHERE id = ?", (nid,)).fetchone()
            if existing is not None:
                return _to_wire(existing)
            payload_value = _bounded_payload(payload)
            conn.execute(
                "INSERT INTO mailbox_notes (id, kind, to_json, sender_json, to_handle, sender_handle, title, body, "
                "payload_json, status, reply, room, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', '', ?, ?, ?)",
                (nid, str(kind or "task"), json.dumps(to_party), json.dumps(sender_party),
                 to_party.get("handle", ""), sender_party.get("handle", ""),
                 clean_title[:TITLE_MAX_CHARS], str(body or "")[:BODY_MAX_CHARS],
                 json.dumps(payload_value) if payload_value is not None else None,
                 str(room) if room else None, now, now))
            row = conn.execute("SELECT * FROM mailbox_notes WHERE id = ?", (nid,)).fetchone()
        return _to_wire(row)

    def get(self, note_id: str) -> dict[str, Any] | None:
        with self._store.read() as conn:
            row = conn.execute("SELECT * FROM mailbox_notes WHERE id = ?", (note_id,)).fetchone()
        return _to_wire(row) if row is not None else None

    def list(self, handle: str | None = None) -> list[dict[str, Any]]:
        want = _normalize_handle(handle)
        with self._store.read() as conn:
            if want:
                rows = conn.execute(
                    "SELECT * FROM mailbox_notes WHERE lower(to_handle) = ? OR lower(sender_handle) = ? "
                    "ORDER BY created_at DESC, id DESC", (want, want)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM mailbox_notes ORDER BY created_at DESC, id DESC").fetchall()
        return [_to_wire(row) for row in rows]

    def update(self, note_id: str, *, status: str, reply: str = "") -> dict[str, Any]:
        want = str(status or "").strip().lower()
        if want not in NOTE_STATUSES:
            raise ValueError(f"unknown status '{status}' — one of {', '.join(NOTE_STATUSES)}")
        clean_reply = str(reply or "")[:BODY_MAX_CHARS]
        with self._store.write() as conn:
            row = conn.execute("SELECT * FROM mailbox_notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise MailboxNotFound(note_id)
            current = row["status"]
            if want != current and want not in _TRANSITIONS[current]:
                raise MailboxTransitionError(f"note '{note_id}' is {current} — cannot become {want}")
            now = int(time.time())
            changed = want != current or bool(clean_reply)
            conn.execute(
                "UPDATE mailbox_notes SET status = ?, reply = CASE WHEN ? != '' THEN ? ELSE reply END, "
                "updated_at = ? WHERE id = ?",
                (want, clean_reply, clean_reply, now if changed else row["updated_at"], note_id))
            row = conn.execute("SELECT * FROM mailbox_notes WHERE id = ?", (note_id,)).fetchone()
        return _to_wire(row)
