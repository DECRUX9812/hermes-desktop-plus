"""Live owner delivery for bot mode and agent mailbox."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_plus.adapter.paths import hermes_home

log = logging.getLogger(__name__)

DELIVERY_DIR_NAME = "bot_live_delivery"
_OWNER_KEYS = ("profile_home", "session_id", "lease_id", "live_session_id")


def find_canonical_owner(profile_home: Path | str) -> dict[str, Any] | None:
    """Find the active lease for Bot Chat in profile_home."""
    home = Path(profile_home).resolve()
    db_path = home / "state.db"
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT id FROM sessions WHERE title = 'Bot Chat' LIMIT 1").fetchone()
            if not row:
                return None
            session_id = row[0]
            # Follow compression lineage tip if any
            cur = conn.execute(
                "SELECT id FROM sessions WHERE parent_session_id = ? ORDER BY started_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            while cur:
                session_id = cur[0]
                cur = conn.execute(
                    "SELECT id FROM sessions WHERE parent_session_id = ? ORDER BY started_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
    except Exception:
        log.debug("failed to query state.db for Bot Chat in %s", home, exc_info=True)
        return None

    active_path = home / "runtime" / "active_sessions.json"
    if not active_path.is_file():
        return None
    try:
        data = json.loads(active_path.read_text(encoding="utf-8"))
        entries = data.get("entries", []) if isinstance(data, dict) else []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("session_id") == session_id:
                return {**entry, "profile_home": str(home)}
    except Exception:
        log.debug("failed to read active_sessions.json in %s", home, exc_info=True)
        return None
    return None


def find_canonical_live_owner(profile_home: Path | str) -> dict[str, Any] | None:
    """Only advertised consumers may receive owner-pinned mailbox deliveries."""
    entry = find_canonical_owner(profile_home)
    meta = (entry or {}).get("metadata") or {}
    if entry and meta.get("bot_live_delivery_consumer") is True and meta.get("live_session_id"):
        return {key: entry[key] for key in ("profile_home", "session_id", "lease_id") if key in entry} | {
            "live_session_id": meta["live_session_id"]
        }
    return None


def deliver_to_live_owner(
    profile_home: Path | str,
    owner: dict[str, Any],
    message: str,
    *,
    delivery_id: str | None = None,
    author: dict[str, Any] | None = None,
    notification_category: str = "result",
) -> dict[str, Any]:
    """Admit a delivery record into the live session owner's inbox."""
    home = Path(profile_home).resolve()
    key = delivery_id or uuid.uuid4().hex
    root = home / "runtime" / DELIVERY_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    ticket_path = root / f"{key}.json"
    record: dict[str, Any] = {
        "delivery_id": key,
        "id": key,
        "owner": owner,
        **{k: owner[k] for k in _OWNER_KEYS if k in owner},
        "message": message,
        "status": "queued",
        "created_at": time.time_ns(),
        "sequence": int(time.time()),
    }
    if author:
        record["author"] = dict(author)
    if notification_category == "diagnostic":
        record["notification_category"] = notification_category

    tmp = root / f".{key}.json.tmp"
    tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    tmp.replace(ticket_path)
    return record


def notify_sender_status(note: dict[str, Any], home_override: Path | str | None = None) -> None:
    """Deliver a single-line status notification to the note sender if it is a live local bot."""
    sender = note.get("sender") or {}
    if str(sender.get("kind") or "") != "bot":
        return
    from_profile = str(sender.get("profile") or "").strip()
    if not from_profile:
        return
    try:
        home = Path(home_override) if home_override is not None else hermes_home(from_profile)
    except Exception:
        return

    owner = find_canonical_live_owner(home)
    if owner is None:
        return

    flat = lambda s: " ".join(str(s or "").split())
    reply = flat(note.get("reply"))
    text = (
        f"Task {note['id']} ({flat(note.get('title'))[:120]}) → {note['status']}"
        + (f": {reply[:500]}" if reply else "")
    )
    handle = sender.get("handle") or from_profile
    author = {"id": f"bot:{from_profile}", "name": str(handle), "is_bot": True}
    deliver_to_live_owner(home, owner, text, author=author)
