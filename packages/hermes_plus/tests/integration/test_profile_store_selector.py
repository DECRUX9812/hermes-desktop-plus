"""A seat's `profile` is DATA: it must not re-point the call at that bot's plus.db.

Reproduces the S25 defect over a real sidecar. `server/multiplexer.py::_dispatch_local`
read `params['profile']` as the profile-store selector for every local method, so
adding a teammate whose profile was `proof-bot` looked the team up in proof-bot's own
(empty) plus.db and answered "no team 'team_…'" — the path a user hits from the Team
page's Add-a-seat form (`plugins/hermes-bots/team-org.tsx`, which sends `profile`).
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import Sidecar, _ws_call, _ws_open

pytestmark = pytest.mark.integration

BOT = "proof-bot"


async def _ensure_bot_profile(ws, name: str) -> None:
    """Make `name` a real profile on this install, the way the app's roster would see it."""
    listed = await _ws_call(ws, "profiles.list", {})
    assert "error" not in listed, listed
    if name in [row["name"] for row in listed["result"]["profiles"]]:
        return
    created = await _ws_call(ws, "profiles.create", {
        "name": name,
        "clone_from": "default",
        "description": "Second profile for the store-selector test",
        "no_alias": True,
    })
    assert "error" not in created, created


def _team_ids(plus_db) -> list[str]:
    if not plus_db.is_file():
        return []
    with sqlite3.connect(plus_db) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM bot_teams")]


@pytest.mark.asyncio
async def test_seating_a_teammate_by_profile_keeps_the_connections_store(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        await _ensure_bot_profile(ws, BOT)

        created = await _ws_call(ws, "bots_team.create", {"name": "Seat Demo", "actor": "you"})
        assert "error" not in created, created
        team_id = created["result"]["team"]["id"]

        # The real UI call: the active gateway, with the seat's own profile as data.
        added = await _ws_call(ws, "bots_team.member.upsert", {
            "team_id": team_id,
            "profile": BOT,
            "role": "proofer",
            "title": BOT,
        })
        assert "error" not in added, added
        assert added["result"]["member"]["profile"] == BOT

        # Read back on the same connection: the team and its seat are both there.
        got = await _ws_call(ws, "bots_team.get", {"team_id": team_id})
        assert "error" not in got, got
        members = got["result"]["team"]["members"]
        assert [member["profile"] for member in members] == [BOT]

        listed = await _ws_call(ws, "bots_team.list", {"profile": BOT})
        assert team_id in [team["id"] for team in listed["result"]["teams"]]

    # The seat's profile is data, not a store selector: the team was not filed in
    # that bot's own plus.db, which only exists because this test named it.
    assert _team_ids(sidecar.home / "profiles" / BOT / "hermes-plus" / "plus.db") == []


@pytest.mark.asyncio
async def test_a_mailbox_note_sender_profile_does_not_pick_the_store(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        await _ensure_bot_profile(ws, BOT)

        sent = await _ws_call(ws, "bots_mailbox.send", {
            "to": "researcher",
            "title": "Store selector",
            "body": "Filed on the connection, not on the sender's profile.",
            "profile": BOT,
        })
        assert "error" not in sent, sent
        note_id = sent["result"]["note"]["id"]

        listed = await _ws_call(ws, "bots_mailbox.list", {})
        assert note_id in [note["id"] for note in listed["result"]["notes"]]

    bot_db = sidecar.home / "profiles" / BOT / "hermes-plus" / "plus.db"
    if bot_db.is_file():
        with sqlite3.connect(bot_db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM mailbox_notes").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_a_profile_scoped_method_still_takes_its_param_as_the_selector(sidecar: Sidecar):
    """fork.memory.* addresses THAT profile's real MEMORY.md (the fork's @_profile_scoped)."""
    async with await _ws_open(sidecar) as ws:
        await _ensure_bot_profile(ws, BOT)

        written = await _ws_call(ws, "fork.memory.add", {
            "profile": BOT, "target": "memory", "text": "Prefer short answers",
        })
        assert "error" not in written, written

        read = await _ws_call(ws, "fork.memory.list", {"profile": BOT})
        assert [entry["text"] for entry in read["result"]["entries"]] == ["Prefer short answers"]

        # The other profile's memory is a different file, not the same store.
        other = await _ws_call(ws, "fork.memory.list", {"profile": "default"})
        assert "error" not in other, other
        assert "Prefer short answers" not in [entry["text"] for entry in other["result"]["entries"]]

    memory_file = sidecar.home / "profiles" / BOT / "memories" / "MEMORY.md"
    assert memory_file.is_file()
    assert "Prefer short answers" in memory_file.read_text(encoding="utf-8")
