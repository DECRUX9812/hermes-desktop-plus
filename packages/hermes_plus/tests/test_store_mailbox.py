from __future__ import annotations

import pytest

from hermes_plus.store import Mailbox, MailboxNotFound, MailboxTransitionError


def _append(mailbox: Mailbox, to: str, note_id: str | None = None) -> dict:
    return mailbox.append(to={"kind": "bot", "profile": to, "handle": to}, sender={"kind": "user"},
                          title="Draft the brief", note_id=note_id)


def test_append_is_idempotent_by_id_and_keeps_status(store):
    mailbox = Mailbox(store)
    first = _append(mailbox, "demo", note_id="note_1")
    assert first["status"] == "open"
    mailbox.update("note_1", status="accepted", reply="on it")
    again = _append(mailbox, "demo", note_id="note_1")
    assert again["status"] == "accepted"
    assert len(mailbox.list()) == 1


def test_list_narrows_to_a_handle(store):
    mailbox = Mailbox(store)
    _append(mailbox, "demo")
    _append(mailbox, "other")
    assert [note["to"]["profile"] for note in mailbox.list("@demo")] == ["demo"]


def test_lifecycle_moves_forward_and_done_is_terminal(store):
    mailbox = Mailbox(store)
    _append(mailbox, "demo", note_id="note_1")
    assert mailbox.update("note_1", status="accepted")["status"] == "accepted"
    assert mailbox.update("note_1", status="done")["status"] == "done"
    with pytest.raises(MailboxTransitionError):
        mailbox.update("note_1", status="open")


def test_unknown_status_is_a_value_error(store):
    mailbox = Mailbox(store)
    _append(mailbox, "demo", note_id="note_1")
    with pytest.raises(ValueError):
        mailbox.update("note_1", status="archived")


def test_updating_an_unknown_note_raises(store):
    with pytest.raises(MailboxNotFound):
        Mailbox(store).update("note_missing", status="done")
