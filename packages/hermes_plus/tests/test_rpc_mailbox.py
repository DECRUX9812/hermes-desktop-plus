from __future__ import annotations

from support import fault, ok


async def _file_note(call, to: str = "demo", title: str = "Draft the brief") -> dict:
    reply = await call("bots_mailbox.send", {"to": to, "title": title, "body": "Two pages.", "profile": "lead"})
    error = fault(reply)
    assert error["code"] == -32004
    assert error["data"]["capability"] == "bot_relay.deliver"
    return error["data"]["note"]


async def test_send_requires_target_and_title(call):
    assert fault(await call("bots_mailbox.send", {"to": "demo"}))["code"] == 4100
    assert fault(await call("bots_mailbox.send", {"title": "Draft"}))["code"] == 4100


async def test_send_files_the_note_then_reports_delivery_unavailable(call):
    note = await _file_note(call, to="@demo")
    assert note["status"] == "open"
    assert note["to"]["profile"] == "demo"
    notes = ok(await call("bots_mailbox.list"))["notes"]
    assert [row["id"] for row in notes] == [note["id"]]


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
