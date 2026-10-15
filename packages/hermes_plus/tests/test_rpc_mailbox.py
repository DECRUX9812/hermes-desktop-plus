from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from support import fault, ok


async def _file_note(call, to: str = "demo", title: str = "Draft the brief") -> dict:
    res = ok(await call("bots_mailbox.send", {"to": to, "title": title, "body": "Two pages.", "profile": "lead"}))
    return res["note"]


async def test_send_requires_target_and_title(call):
    assert fault(await call("bots_mailbox.send", {"to": "demo"}))["code"] == 4100
    assert fault(await call("bots_mailbox.send", {"title": "Draft"}))["code"] == 4100


async def test_send_files_the_note(call):
    note = await _file_note(call, to="@demo")
    assert note["status"] == "open"
    assert note["to"]["profile"] == "demo"
    notes = ok(await call("bots_mailbox.list"))["notes"]
    assert [row["id"] for row in notes] == [note["id"]]


async def test_send_upstream_delivery_success(ctx, call):
    mock_upstream = AsyncMock()
    mock_upstream.call.return_value = {"reply": "Accepted by target bot"}
    ctx.upstream = mock_upstream

    res = ok(await call("bots_mailbox.send", {"to": "demo", "title": "Review PR", "body": "Look at #42"}))
    assert res["note"]["title"] == "Review PR"
    assert res["reply"] == "Accepted by target bot"
    assert "delivery_error" not in res

    mock_upstream.call.assert_awaited_once()
    method_name, payload = mock_upstream.call.call_args[0]
    assert method_name == "bot_relay.deliver"
    assert payload["profile"] == "demo"
    assert "Task hand-off from the user" in payload["message"]
    assert f"[task {res['note']['id']}" in payload["message"]


async def test_send_upstream_delivery_failure_retains_note(ctx, call):
    mock_upstream = AsyncMock()
    mock_upstream.call.side_effect = RuntimeError("target profile offline")
    ctx.upstream = mock_upstream

    res = ok(await call("bots_mailbox.send", {"to": "demo", "title": "Urgent task"}))
    assert res["note"]["title"] == "Urgent task"
    assert "delivery_error" in res
    assert "target profile offline" in res["delivery_error"]

    # The note must still be safely in the store
    notes = ok(await call("bots_mailbox.list"))["notes"]
    assert res["note"]["id"] in [n["id"] for n in notes]


async def test_send_and_update_emit_gateway_events(ctx, call):
    emitted = []

    async def mock_emit(event_name: str, payload: dict[str, Any], session_id: str | None = None) -> None:
        emitted.append((event_name, payload))

    ctx.emit = mock_emit

    res = ok(await call("bots_mailbox.send", {"to": "demo", "title": "Event test"}))
    note_id = res["note"]["id"]
    assert ("bot_relay.outbox.pending", {}) in emitted
    assert any(e[0] == "mailbox.updated" and e[1]["note"]["id"] == note_id for e in emitted)

    emitted.clear()
    ok(await call("bots_mailbox.update", {"id": note_id, "status": "accepted"}))
    assert ("bot_relay.outbox.pending", {}) in emitted
    assert any(e[0] == "mailbox.updated" and e[1]["note"]["status"] == "accepted" for e in emitted)


async def test_update_notifies_live_owner(monkeypatch, ctx, call):
    from hermes_plus.adapter import live_delivery

    delivered = {}

    def fake_find(home):
        return {"profile_home": str(home), "session_id": "sess-live", "lease_id": "lease-1", "live_session_id": "live-1"}

    def fake_deliver(home, owner, message, author=None):
        delivered["home"] = home
        delivered["owner"] = owner
        delivered["message"] = message
        delivered["author"] = author
        return {"id": "deliv-1"}

    monkeypatch.setattr(live_delivery, "find_canonical_live_owner", fake_find)
    monkeypatch.setattr(live_delivery, "deliver_to_live_owner", fake_deliver)

    # Insert note sent by a bot
    note = ctx.mailbox.append(
        to={"kind": "user", "profile": "user", "handle": "user"},
        sender={"kind": "bot", "profile": "researcher", "handle": "researcher"},
        title="Multitask\nnewline\nin title",
        body="details",
    )

    ok(await call("bots_mailbox.update", {
        "id": note["id"],
        "status": "done",
        "reply": "Result\nwith\nmultiple\nlines",
    }))

    assert delivered["owner"]["session_id"] == "sess-live"
    assert delivered["author"] == {"id": "bot:researcher", "name": "researcher", "is_bot": True}
    # Verify title and reply are flattened (no raw newlines)
    assert "\n" not in delivered["message"]
    assert "Task " in delivered["message"]
    assert "Multitask newline in title" in delivered["message"]
    assert "done" in delivered["message"]
    assert "Result with multiple lines" in delivered["message"]


async def test_list_filters_by_handle(call):
    await _file_note(call, to="demo")
    await _file_note(call, to="other")
    notes = ok(await call("bots_mailbox.list", {"handle": "@demo"}))["notes"]
    assert [note["to"]["profile"] for note in notes] == ["demo"]


async def test_update_requires_id_and_status(call):
    assert fault(await call("bots_mailbox.update", {"status": "done"}))["code"] == 4102


async def test_update_unknown_note_is_not_found(call):
    assert fault(await call("bots_mailbox.update", {"id": "note_missing", "status": "done"}))["code"] == 4103


async def test_update_follows_the_lifecycle_and_refuses_illegal_moves(call):
    note = await _file_note(call)
    accepted = ok(await call("bots_mailbox.update", {"id": note["id"], "status": "accepted", "reply": "on it"}))
    assert accepted["note"]["status"] == "accepted"
    assert accepted["note"]["reply"] == "on it"
    done = ok(await call("bots_mailbox.update", {"id": note["id"], "status": "done"}))
    assert done["note"]["status"] == "done"
    assert fault(await call("bots_mailbox.update", {"id": note["id"], "status": "open"}))["code"] == 4104
    assert fault(await call("bots_mailbox.update", {"id": note["id"], "status": "archived"}))["code"] == 4104
