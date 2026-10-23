"""Group Rooms plugin proof: the plugin loads through register() with a fake ctx,
honours its config_schema defaults (and explicit settings), and runs the room
lifecycle create room -> add 2 seats -> post -> thread -> state."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "hermes_plus" / "extras" / "plugins" / "group-rooms"


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


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin()


@pytest.fixture
def rooms(plugin, tmp_path: Path):
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    yield plugin
    plugin.close()


def test_manifest_declares_the_room_knobs(plugin) -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "group-rooms"
    assert manifest["requires_hermes"] == ">=0.22.0"
    schema = manifest["config_schema"]
    for knob in ("max_seats", "default_room_mode", "thread_rail_enabled", "max_message_chars"):
        assert knob in schema, f"config_schema is missing {knob}"
        assert schema[knob]["default"] == plugin.DEFAULTS[knob], (
            f"plugin.yaml default for {knob} drifted from the code's DEFAULTS")


def test_schema_defaults_are_honoured(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx()  # no settings at all: every knob must fall to its schema default
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    try:
        room = plugin.create_room("ops")
        assert room["mode"] == "collab"

        # max_seats default is 6: six seats fit, the seventh is refused.
        for index in range(6):
            plugin.add_seat("ops", f"bot-{index}")
        with pytest.raises(plugin.SeatLimitError):
            plugin.add_seat("ops", "bot-6")

        # thread_rail_enabled defaults to true: starting a thread works.
        thread = plugin.start_thread("ops", title="plan")
        assert thread["room_id"] == room["id"]
    finally:
        plugin.close()


def test_settings_override_schema_defaults(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx({"max_seats": 2, "default_room_mode": "lead", "thread_rail_enabled": False})
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    try:
        assert plugin.create_room("board")["mode"] == "lead"
        plugin.add_seat("board", "lead-bot", role="lead")
        plugin.add_seat("board", "second-bot")
        with pytest.raises(plugin.SeatLimitError):
            plugin.add_seat("board", "third-bot")
        with pytest.raises(plugin.RoomError, match="thread rail is disabled"):
            plugin.start_thread("board")
    finally:
        plugin.close()


def test_room_lifecycle_seats_post_thread_state(rooms) -> None:
    room = rooms.create_room("port-crew", goal="port the fork's group rooms")
    assert room["mode"] == "collab"

    rooms.add_seat("port-crew", "atlas", role="lead")
    rooms.add_seat("port-crew", "vex")
    assert [seat["bot"] for seat in rooms.room_state("port-crew")["seats"]] == ["atlas", "vex"]

    posted = rooms.post_message("port-crew", "user", "map the fork's room model")
    routed = rooms.route_message("port-crew", posted["id"])
    assert [seat["bot"] for seat in routed] == ["atlas", "vex"]  # collab: every other seat
    assert all(seat["status"] == "pending" for seat in routed)
    assert rooms.room_state("port-crew")["messages"][0]["routed_to"] == ["atlas", "vex"]

    thread = rooms.start_thread("port-crew", title="handoff plan", origin_message_id=posted["id"])
    reply = rooms.post_message(
        "port-crew", "atlas", "seats map to profiles; routing picks the answerer",
        thread_id=thread["id"])
    assert reply["thread_id"] == thread["id"]
    # atlas was routed (pending) and then spoke: live status advanced to working.
    assert rooms.seat("port-crew", "atlas")["status"] == "working"
    rooms.set_seat_status("port-crew", "atlas", "done", note="answer posted")

    moved = rooms.handoff("port-crew", thread["id"], "atlas", "vex", note="follow-up questions")
    assert moved["open"] is True
    assert rooms.seat("port-crew", "vex")["status"] == "pending"

    state = rooms.room_state("port-crew")
    assert state["room"]["name"] == "port-crew"
    assert state["room"]["goal"] == "port the fork's group rooms"
    assert {seat["bot"]: seat["status"] for seat in state["seats"]} == {
        "atlas": "done", "vex": "pending"}
    assert [thread_["title"] for thread_ in state["threads"]] == ["handoff plan"]
    assert len(state["messages"]) == 2
    assert state["handoffs"][0]["to_bot"] == "vex"
    assert state["handoffs"][0]["open"] is True

    rooms.resolve_handoff("port-crew", moved["id"])
    assert rooms.room_state("port-crew")["handoffs"][0]["open"] is False

    assert [row["name"] for row in rooms.list_rooms()] == ["port-crew"]
    assert rooms.list_rooms()[0]["seat_count"] == 2
