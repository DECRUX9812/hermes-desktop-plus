"""bots_mailbox.{list,send,update}: install-wide task notes between bots and the user."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from hermes_plus.adapter.live_delivery import notify_sender_status
from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import (
    MAILBOX_ILLEGAL_TRANSITION,
    MAILBOX_LIST_FAILED,
    MAILBOX_NOTE_NOT_FOUND,
    MAILBOX_SEND_FAILED,
    MAILBOX_SEND_PARAMS,
    MAILBOX_UPDATE_FAILED,
    MAILBOX_UPDATE_PARAMS,
    RpcFault,
)
from hermes_plus.rpc.params import read_text
from hermes_plus.rpc.registry import method
from hermes_plus.store import MailboxNotFound

log = logging.getLogger(__name__)


async def _safe_emit(ctx: RpcContext, event_name: str, payload: dict[str, Any]) -> None:
    if ctx.emit is not None:
        try:
            res = ctx.emit(event_name, payload)
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass


@method("bots_mailbox.list")
def list_notes(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    handle = read_text(params, "handle") or None
    try:
        return {"notes": ctx.mailbox.list(handle)}
    except Exception as err:
        raise RpcFault(MAILBOX_LIST_FAILED, str(err)) from err


@method("bots_mailbox.send")
async def send_note(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    target = read_text(params, "to").lstrip("@")
    title = read_text(params, "title")
    if not target or not title:
        raise RpcFault(MAILBOX_SEND_PARAMS, "to and title required")
    sender = {"kind": "user", "name": "You", "profile": read_text(params, "profile")}
    body = read_text(params, "body")
    try:
        note = await asyncio.to_thread(
            ctx.mailbox.append,
            to={"kind": "bot", "profile": target, "handle": target.lower()},
            sender=sender,
            title=title,
            body=body,
            payload=params.get("payload"),
        )
    except Exception as err:
        raise RpcFault(MAILBOX_SEND_FAILED, str(err)) from err

    flat_title = " ".join(title.split())
    text = (
        f"Task hand-off from the user (via Desktop): {flat_title}"
        + (f"\n\n{body}" if body else "")
        + f"\n\n[task {note['id']} — call update_task(note=…, status=…) to accept/decline/finish it]"
    )

    reply = ""
    delivery_error: str | None = None
    upstream = ctx.upstream or ctx.internal
    if upstream is not None:
        try:
            delivered = await upstream.call(
                "bot_relay.deliver",
                {
                    "profile": target,
                    "message": text,
                    "from_handle": "you",
                },
            )
            if isinstance(delivered, dict):
                reply = str(delivered.get("reply") or "")
        except Exception as exc:
            delivery_error = getattr(exc, "message", str(exc))

    await _safe_emit(ctx, "bot_relay.outbox.pending", {})
    await _safe_emit(ctx, "mailbox.updated", {"note": note})

    res: dict[str, Any] = {"note": note}
    if delivery_error is not None:
        res["delivery_error"] = delivery_error
    else:
        res["reply"] = reply
    return res


@method("bots_mailbox.update")
async def update_note(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    note_id = read_text(params, "id")
    status = read_text(params, "status")
    if not note_id or not status:
        raise RpcFault(MAILBOX_UPDATE_PARAMS, "id and status required")
    try:
        note = await asyncio.to_thread(
            ctx.mailbox.update,
            note_id,
            status=status,
            reply=str(params.get("reply") or ""),
        )
    except MailboxNotFound as err:
        raise RpcFault(MAILBOX_NOTE_NOT_FOUND, f"no mailbox note '{note_id}'") from err
    except ValueError as err:
        raise RpcFault(MAILBOX_ILLEGAL_TRANSITION, str(err)) from err
    except Exception as err:
        raise RpcFault(MAILBOX_UPDATE_FAILED, str(err)) from err

    try:
        notify_sender_status(note)
    except Exception:
        log.debug("bots_mailbox.update status notify failed", exc_info=True)

    await _safe_emit(ctx, "bot_relay.outbox.pending", {})
    await _safe_emit(ctx, "mailbox.updated", {"note": note})

    return {"note": note}
