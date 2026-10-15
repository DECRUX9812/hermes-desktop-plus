from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermes_plus.adapter.live_delivery import (
    deliver_to_live_owner,
    find_canonical_live_owner,
    find_canonical_owner,
    notify_sender_status,
)


def test_find_canonical_owner_empty_home(tmp_path: Path):
    assert find_canonical_owner(tmp_path) is None
    assert find_canonical_live_owner(tmp_path) is None


def test_find_canonical_live_owner_with_active_session(tmp_path: Path):
    # Setup state.db
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                parent_session_id TEXT,
                started_at REAL
            )
        """)
        conn.execute("INSERT INTO sessions (id, title, started_at) VALUES ('sess-root', 'Bot Chat', 100.0)")
        # Add compression descendant
        conn.execute("INSERT INTO sessions (id, title, parent_session_id, started_at) VALUES ('sess-tip', 'Bot Chat', 'sess-root', 200.0)")

    # Setup active_sessions.json without bot_live_delivery_consumer
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    active_path = runtime_dir / "active_sessions.json"
    active_path.write_text(json.dumps({
        "entries": [
            {
                "session_id": "sess-tip",
                "lease_id": "lease-abc",
                "metadata": {"live_session_id": "live-xyz"},
            }
        ]
    }))

    # Canonical owner found, but not live consumer
    owner = find_canonical_owner(tmp_path)
    assert owner is not None
    assert owner["session_id"] == "sess-tip"
    assert find_canonical_live_owner(tmp_path) is None

    # Enable consumer flag
    active_path.write_text(json.dumps({
        "entries": [
            {
                "session_id": "sess-tip",
                "lease_id": "lease-abc",
                "metadata": {"live_session_id": "live-xyz", "bot_live_delivery_consumer": True},
            }
        ]
    }))

    live_owner = find_canonical_live_owner(tmp_path)
    assert live_owner is not None
    assert live_owner["session_id"] == "sess-tip"
    assert live_owner["lease_id"] == "lease-abc"
    assert live_owner["live_session_id"] == "live-xyz"


def test_deliver_to_live_owner_writes_ticket(tmp_path: Path):
    owner = {
        "profile_home": str(tmp_path),
        "session_id": "sess-tip",
        "lease_id": "lease-abc",
        "live_session_id": "live-xyz",
    }
    author = {"id": "bot:researcher", "name": "researcher", "is_bot": True}
    record = deliver_to_live_owner(
        tmp_path,
        owner,
        "Task completed successfully",
        author=author,
    )
    delivery_id = record["delivery_id"]
    ticket_file = tmp_path / "runtime" / "bot_live_delivery" / f"{delivery_id}.json"
    assert ticket_file.is_file()
    data = json.loads(ticket_file.read_text(encoding="utf-8"))
    assert data["message"] == "Task completed successfully"
    assert data["session_id"] == "sess-tip"
    assert data["author"] == author


def test_notify_sender_status_end_to_end(tmp_path: Path):
    # Set up live bot home
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, parent_session_id TEXT, started_at REAL)")
        conn.execute("INSERT INTO sessions (id, title, started_at) VALUES ('sess-bot', 'Bot Chat', 100.0)")

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "active_sessions.json").write_text(json.dumps({
        "entries": [
            {
                "session_id": "sess-bot",
                "lease_id": "lease-123",
                "metadata": {"live_session_id": "live-bot", "bot_live_delivery_consumer": True},
            }
        ]
    }))

    note = {
        "id": "note-99",
        "status": "done",
        "title": "Clean codebase\nand run tests",
        "reply": "All done\ncleanly",
        "sender": {
            "kind": "bot",
            "profile": "worker",
            "handle": "worker",
        },
    }

    notify_sender_status(note, home_override=tmp_path)

    delivery_dir = tmp_path / "runtime" / "bot_live_delivery"
    tickets = list(delivery_dir.glob("*.json"))
    assert len(tickets) == 1
    ticket = json.loads(tickets[0].read_text(encoding="utf-8"))
    assert ticket["message"] == "Task note-99 (Clean codebase and run tests) → done: All done cleanly"
    assert ticket["author"] == {"id": "bot:worker", "name": "worker", "is_bot": True}
