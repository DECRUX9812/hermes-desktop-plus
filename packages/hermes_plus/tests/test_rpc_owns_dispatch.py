"""registry.owns and registry.dispatch: the multiplexer-facing surface."""

from __future__ import annotations

from hermes_plus.rpc import dispatch, owns


def test_owns_registered_handlers_and_namespaces():
    assert owns("bots_team.create")
    assert owns("fork.approvals.decide")
    assert owns("fork.memory.anything")  # namespace prefix
    assert owns("bots_mailbox.list")
    assert owns("fork.routines.list")
    assert owns("session.ask")
    assert owns("session.predict_next")


def test_owns_rejects_upstream_and_unknown_names():
    assert not owns("session.list")
    assert not owns("session.create")
    assert not owns("config.get")
    assert not owns("fork.unknown.deep")
    assert not owns("")


async def test_dispatch_returns_response_objects(ctx):
    reply = await dispatch(
        ctx, {"jsonrpc": "2.0", "id": "x1", "method": "bots_team.list", "params": {}}
    )
    assert reply == {"jsonrpc": "2.0", "id": "x1", "result": {"teams": []}}


async def test_dispatch_notification_returns_none(ctx):
    reply = await dispatch(ctx, {"jsonrpc": "2.0", "method": "bots_team.list"})
    assert reply is None


async def test_dispatch_unknown_owned_method_is_method_not_found(ctx):
    reply = await dispatch(ctx, {"jsonrpc": "2.0", "id": 9, "method": "fork.memory.nope"})
    assert reply["id"] == 9
    assert reply["error"]["code"] == -32601


async def test_dispatch_invalid_request(ctx):
    reply = await dispatch(ctx, {"jsonrpc": "1.0", "id": 3, "method": "bots_team.list"})
    assert reply["error"]["code"] == -32600
