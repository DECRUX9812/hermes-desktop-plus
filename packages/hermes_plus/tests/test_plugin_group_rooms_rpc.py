"""Group Rooms RPC proof: the plugin registers its room API as JSON-RPC methods
through the sidecar registry's sanctioned plugin hook at register(ctx) time, and
the full lifecycle runs end to end over plain `handle_text` dispatch —
create room -> 2 seats -> post -> thread -> state -> handoff/resolve — with
param validation and plugin-domain fault codes.

The plugin's methods live under `plugin.group_rooms.` and un-register on
close(), so the contracted import-time surface (test_rpc_registry) is untouched.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from hermes_plus.rpc import handle_text, methods
from hermes_plus.rpc.registry import unregister_plugin_methods

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "hermes_plus" / "extras" / "plugins" / "group-rooms"

ROOM_NOT_FOUND = 4200
ROOM_REFUSED = 4201


def _load_plugin():
    """Load the plugin the way a host would: as a standalone directory module."""
    spec = importlib.util.spec_from_file_location(
        "group_rooms_plugin", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # so `from .rooms import ...` resolves mid-exec
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Minimal plugin host: settings store + captured hooks + a data dir."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = dict(settings or {})
        self.data_dir: Path | None = None
        self.hooks: dict[str, Any] = {}

    def get_config(self, name: str, default: Any = None) -> Any:
        return self.settings.get(name, default)

    def register_hook(self, name: str, fn: Any) -> None:
        self.hooks[name] = fn


@pytest.fixture
def wired(plugin, tmp_path: Path):
    """The plugin registered against a fake host: RPC methods land in the
    sidecar registry, the store lives in tmp; everything un-registers after."""
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    yield plugin
    plugin.close()
    assert unregister_plugin_methods("plugin.group_rooms.") == 0  # close() dropped them all


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin()


async def _rpc(ctx: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(await handle_text(ctx, json.dumps(request)))


async def test_register_lands_the_plugin_methods(wired, ctx) -> None:
    names = [name for name in methods() if name.startswith("plugin.group_rooms.")]
    assert len(names) == 11
    for surface in (
        "plugin.group_rooms.rooms.list", "plugin.group_rooms.rooms.create", "plugin.group_rooms.rooms.state",
        "plugin.group_rooms.seat.add", "plugin.group_rooms.seat.remove", "plugin.group_rooms.seat.status",
        "plugin.group_rooms.message.post", "plugin.group_rooms.thread.start",
        "plugin.group_rooms.handoff.create", "plugin.group_rooms.handoff.resolve",
        "plugin.group_rooms.config",
    ):
        assert surface in names
    # The registry's owns() claims a plugin method only once it is registered.
    from hermes_plus.rpc.registry import owns
    assert owns("plugin.group_rooms.rooms.list") is True


async def test_room_lifecycle_end_to_end_over_rpc(wired, ctx) -> None:
    # config: the UI gate reads thread_rail_enabled from here.
    reply = await _rpc(ctx, "plugin.group_rooms.config")
    assert reply["result"]["thread_rail_enabled"] is True

    # create room
    reply = await _rpc(ctx, "plugin.group_rooms.rooms.create",
                       {"name": "rpc-crew", "goal": "prove the rpc door"})
    assert reply["result"]["room"]["name"] == "rpc-crew"
    assert reply["result"]["room"]["mode"] == "collab"

    # two seats
    for bot, role in (("atlas", "lead"), ("vex", "member")):
        reply = await _rpc(ctx, "plugin.group_rooms.seat.add", {"room": "rpc-crew", "bot": bot, "role": role})
        assert reply["result"]["seat"]["bot"] == bot
    reply = await _rpc(ctx, "plugin.group_rooms.rooms.state", {"room": "rpc-crew"})
    assert [seat["bot"] for seat in reply["result"]["state"]["seats"]] == ["atlas", "vex"]

    # post + routing still reachable through the room API the handlers call
    reply = await _rpc(ctx, "plugin.group_rooms.message.post",
                       {"room": "rpc-crew", "sender": "user", "text": "map the room model"})
    message = reply["result"]["message"]
    assert message["text"] == "map the room model"

    # thread start (thread_rail_enabled: true by default)
    reply = await _rpc(ctx, "plugin.group_rooms.thread.start",
                       {"room": "rpc-crew", "title": "handoff plan", "origin_message_id": message["id"]})
    thread = reply["result"]["thread"]
    assert thread["title"] == "handoff plan"

    # a seat answers inside the thread, then hands it off and resolves it
    reply = await _rpc(ctx, "plugin.group_rooms.message.post",
                       {"room": "rpc-crew", "sender": "atlas", "text": "seats map to profiles",
                        "thread_id": thread["id"]})
    assert reply["result"]["message"]["thread_id"] == thread["id"]

    reply = await _rpc(ctx, "plugin.group_rooms.handoff.create",
                       {"room": "rpc-crew", "thread_id": thread["id"], "from_bot": "atlas", "to_bot": "vex"})
    handoff = reply["result"]["handoff"]
    assert handoff["open"] is True
    assert handoff["to_bot"] == "vex"

    reply = await _rpc(ctx, "plugin.group_rooms.handoff.resolve",
                       {"room": "rpc-crew", "handoff_id": handoff["id"]})
    assert reply["result"]["handoff"]["open"] is False

    # seat status + remove, then the final state
    reply = await _rpc(ctx, "plugin.group_rooms.seat.status",
                       {"room": "rpc-crew", "bot": "atlas", "status": "done", "note": "answered"})
    assert reply["result"]["seat"]["status"] == "done"
    reply = await _rpc(ctx, "plugin.group_rooms.seat.remove", {"room": "rpc-crew", "bot": "vex"})
    assert reply["result"]["removed"] is True

    reply = await _rpc(ctx, "plugin.group_rooms.rooms.state", {"room": "rpc-crew"})
    state = reply["result"]["state"]
    assert {seat["bot"]: seat["status"] for seat in state["seats"]} == {"atlas": "done"}
    assert [thread_["title"] for thread_ in state["threads"]] == ["handoff plan"]
    assert len(state["messages"]) == 2
    assert state["handoffs"][0]["open"] is False

    reply = await _rpc(ctx, "plugin.group_rooms.rooms.list")
    assert [row["name"] for row in reply["result"]["rooms"]] == ["rpc-crew"]


async def test_param_validation_is_invalid_params(wired, ctx) -> None:
    for surface, params in (
        ("plugin.group_rooms.rooms.create", {"goal": "no name"}),
        ("plugin.group_rooms.rooms.state", {}),
        ("plugin.group_rooms.seat.add", {"room": "rpc-crew"}),
        ("plugin.group_rooms.message.post", {"room": "rpc-crew", "sender": "user"}),
        ("plugin.group_rooms.thread.start", {}),
        ("plugin.group_rooms.handoff.resolve", {"room": "rpc-crew"}),
        ("plugin.group_rooms.handoff.create", {"room": "rpc-crew", "thread_id": "one", "from_bot": "a", "to_bot": "b"}),
    ):
        reply = await _rpc(ctx, surface, params)
        assert reply["error"]["code"] == -32602, surface
    # non-object params are refused by the protocol itself
    reply = json.loads(await handle_text(ctx, json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "plugin.group_rooms.rooms.list", "params": []})))
    assert reply["error"]["code"] == -32602


async def test_domain_faults_use_the_plugin_codes(wired, ctx) -> None:
    reply = await _rpc(ctx, "plugin.group_rooms.rooms.state", {"room": "ghost"})
    assert reply["error"]["code"] == ROOM_NOT_FOUND

    await _rpc(ctx, "plugin.group_rooms.rooms.create", {"name": "full"})
    for index in range(6):
        assert (await _rpc(ctx, "plugin.group_rooms.seat.add",
                           {"room": "full", "bot": f"bot-{index}"}))["result"]
    reply = await _rpc(ctx, "plugin.group_rooms.seat.add", {"room": "full", "bot": "b7"})
    assert reply["error"]["code"] == ROOM_REFUSED


async def test_thread_rail_disabled_refuses_threads_and_gates_the_config(wired, ctx, tmp_path: Path) -> None:
    ctx2 = FakeCtx({"thread_rail_enabled": False})
    ctx2.data_dir = tmp_path / "off"
    wired.register(ctx2)
    try:
        await _rpc(ctx2, "plugin.group_rooms.rooms.create", {"name": "quiet"})
        reply = await _rpc(ctx2, "plugin.group_rooms.thread.start", {"room": "quiet", "title": "nope"})
        assert reply["error"]["code"] == ROOM_REFUSED
        assert "thread rail is disabled" in reply["error"]["message"]

        reply = await _rpc(ctx2, "plugin.group_rooms.config")
        assert reply["result"]["thread_rail_enabled"] is False
    finally:
        wired.close()


async def test_fresh_registration_replaces_stale_methods(wired, ctx, tmp_path: Path) -> None:
    """A re-register must not raise 'registered twice' — the hook drops the
    plugin's previous methods first."""
    ctx2 = FakeCtx()
    ctx2.data_dir = tmp_path / "again"
    wired.register(ctx2)
    try:
        reply = await _rpc(ctx2, "plugin.group_rooms.rooms.create", {"name": "again"})
        assert reply["result"]["room"]["name"] == "again"
    finally:
        wired.close()
