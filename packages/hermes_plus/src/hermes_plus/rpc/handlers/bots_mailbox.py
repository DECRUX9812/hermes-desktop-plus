"""bots_mailbox.{list,send,update}: install-wide task notes between bots and the user."""

from __future__ import annotations

from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import (
    FEATURE_UNAVAILABLE,
    MAILBOX_ILLEGAL_TRANSITION,
    MAILBOX_LIST_FAILED,
    MAILBOX_NOTE_NOT_FOUND,
    MAILBOX_SEND_FAILED,
    MAILBOX_SEND_PARAMS,
    MAILBOX_UPDATE_FAILED,
    MAILBOX_UPDATE_PARAMS,
    RpcFault,
)
from hermes_plus.rpc.params import read_text, require_text
from hermes_plus.rpc.registry import method
from hermes_plus.store import MailboxNotFound

DELIVERY_CAPABILITY = "bot_relay.deliver"


@method("bots_mailbox.list")
def list_notes(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    handle = read_text(params, "handle") or None
    try:
        return {"notes": ctx.mailbox.list(handle)}
    except Exception as err:
        raise RpcFault(MAILBOX_LIST_FAILED, str(err)) from err


@method("bots_mailbox.send")
def send_note(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    target = read_text(params, "to").lstrip("@")
    title = read_text(params, "title")
    if not target or not title:
        raise RpcFault(MAILBOX_SEND_PARAMS, "to and title required")
    sender = {"kind": "user", "name": "You", "profile": read_text(params, "profile")}
    try:
        note = ctx.mailbox.append(
            to={"kind": "bot", "profile": target, "handle": target.lower()},
            sender=sender, title=title, body=read_text(params, "body"), payload=params.get("payload"))
    except Exception as err:
        raise RpcFault(MAILBOX_SEND_FAILED, str(err)) from err
    raise RpcFault(FEATURE_UNAVAILABLE,
                   f"{DELIVERY_CAPABILITY} is not available, so the note was filed but not delivered",
                   data={"capability": DELIVERY_CAPABILITY, "note": note})


@method("bots_mailbox.update")
def update_note(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    note_id = read_text(params, "id")
    status = read_text(params, "status")
    if not note_id or not status:
        raise RpcFault(MAILBOX_UPDATE_PARAMS, "id and status required")
    try:
        note = ctx.mailbox.update(note_id, status=status, reply=str(params.get("reply") or ""))
    except MailboxNotFound as err:
        raise RpcFault(MAILBOX_NOTE_NOT_FOUND, f"no mailbox note '{note_id}'") from err
    except ValueError as err:
        raise RpcFault(MAILBOX_ILLEGAL_TRANSITION, str(err)) from err
    except Exception as err:
        raise RpcFault(MAILBOX_UPDATE_FAILED, str(err)) from err
    return {"note": note}
