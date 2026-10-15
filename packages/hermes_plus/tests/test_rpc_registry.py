from __future__ import annotations

import json

import pytest

from hermes_plus.rpc import handle_text, methods
from hermes_plus.rpc.registry import method

SURFACE = sorted([
    "fork.bot.identity.get", "fork.bot.identity.set",
    "fork.memory.list", "fork.memory.add", "fork.memory.update", "fork.memory.remove",
    "fork.onboarding.state", "fork.onboarding.mark_seen", "fork.onboarding.record_failed_start",
    "fork.approvals.list", "fork.approvals.decide",
    "bots_team.list", "bots_team.get", "bots_team.room_lead", "bots_team.create", "bots_team.update",
    "bots_team.delete", "bots_team.member.upsert", "bots_team.member.remove", "bots_team.goal.upsert",
    "bots_team.goal.link_task", "bots_team.goal.spawn_task", "bots_team.approval.request",
    "bots_team.approval.decide", "bots_team.budget.check", "bots_team.budget.record",
    "bots_team.learning.add", "bots_team.learning.remove", "bots_team.brief", "bots_team.pack.export",
    "bots_team.pack.import", "bots_team.audit.list",
    "bots_mailbox.list", "bots_mailbox.send", "bots_mailbox.update",
])


def test_surface_is_exactly_the_contracted_methods():
    assert list(methods()) == SURFACE


def test_routines_and_setup_profile_methods_are_not_exposed():
    names = set(methods())
    assert not any(name.startswith("fork.routines.") for name in names)
    assert not names & {
        "fork.onboarding.ensure_setup_profile",
        "fork.onboarding.ensure_setup_session",
        "fork.onboarding.reset_setup_profile",
    }


def test_registry_refuses_names_outside_the_surface():
    with pytest.raises(ValueError):
        method("fork.routines.list")
    with pytest.raises(ValueError):
        method("fork.onboarding.reset_setup_profile")


async def test_unparseable_line_gets_parse_error(ctx):
    reply = json.loads(await handle_text(ctx, "{not json"))
    assert reply == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}


async def test_wrong_protocol_version_is_an_invalid_request(ctx):
    reply = json.loads(await handle_text(ctx, json.dumps(
        {"jsonrpc": "1.0", "id": 7, "method": "fork.memory.list"})))
    assert reply["id"] == 7
    assert reply["error"]["code"] == -32600


async def test_unknown_method_is_method_not_found(call):
    reply = await call("fork.routines.list")
    assert reply["error"]["code"] == -32601


async def test_non_object_params_are_invalid_params(ctx):
    reply = json.loads(await handle_text(ctx, json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "fork.memory.list", "params": []})))
    assert reply["error"]["code"] == -32602


async def test_notification_gets_no_reply(ctx):
    line = json.dumps({"jsonrpc": "2.0", "method": "fork.memory.list", "params": {"profile": "demo"}})
    assert await handle_text(ctx, line) is None


async def test_batch_replies_in_order_and_skips_notifications(ctx):
    batch = [
        {"jsonrpc": "2.0", "id": "a", "method": "fork.onboarding.state"},
        {"jsonrpc": "2.0", "method": "fork.onboarding.state"},
        {"jsonrpc": "2.0", "id": "b", "method": "fork.nope"},
    ]
    replies = json.loads(await handle_text(ctx, json.dumps(batch)))
    assert [reply["id"] for reply in replies] == ["a", "b"]
    assert replies[1]["error"]["code"] == -32601


async def test_batch_of_only_notifications_gets_no_reply(ctx):
    batch = [{"jsonrpc": "2.0", "method": "fork.onboarding.state"}]
    assert await handle_text(ctx, json.dumps(batch)) is None


async def test_empty_batch_is_an_invalid_request(ctx):
    reply = json.loads(await handle_text(ctx, "[]"))
    assert reply["error"]["code"] == -32600
