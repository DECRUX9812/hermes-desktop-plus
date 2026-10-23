"""Home Feed plugin proof: the plugin loads through register() with a fake ctx,
honours its config_schema defaults (and explicit settings), and runs the home
surface end to end over the registered RPC — cards sourced read-only from the
profile's state.db sessions and cron jobs.json, escalations from the registered
command-room plugin and open handoffs from group-rooms when those are present
(and nothing when they are absent), the fork's priority ordering and caps, and
dismiss/pin/restore through the store."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from hermes_plus.rpc import handle_text, methods
from hermes_plus.rpc.registry import unregister_plugin_methods

BASE = Path(__file__).resolve().parents[1] / "src" / "hermes_plus" / "extras" / "plugins"
PLUGIN_DIR = BASE / "home-feed"

CARD_NOT_FOUND = 4220
FEED_REFUSED = 4221
INVALID_PARAMS = -32602


def _load_plugin(name: str, plugin_dir: Path):
    """Load a plugin the way a host would: as a standalone directory module."""
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # so `from .store import ...` resolves mid-exec
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Minimal plugin host: settings store + captured hooks + a data dir + home."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = dict(settings or {})
        self.data_dir: Path | None = None
        self.hermes_home: Path | None = None
        self.hooks: dict[str, Any] = {}

    def get_config(self, name: str, default: Any = None) -> Any:
        return self.settings.get(name, default)

    def register_hook(self, name: str, fn: Any) -> None:
        self.hooks[name] = fn


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin("home_feed_plugin", PLUGIN_DIR)


@pytest.fixture
def wired(plugin, tmp_path: Path):
    """The plugin registered against a fake host: RPC methods land in the
    sidecar registry, the store and the read-only sources live in tmp;
    everything un-registers after (sibling-plugin isolation is autouse)."""
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    yield ctx
    plugin.close()
    assert unregister_plugin_methods("plugin.home_feed.") == 0  # close() dropped them all


async def _rpc(ctx: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(await handle_text(ctx, json.dumps(request)))


def _write_state_db(home: Path, rows: list[dict[str, Any]]) -> None:
    """A minimal stand-in for the profile's state.db: just the sessions columns
    the feed reads. The plugin must open it read-only and never write here."""
    conn = sqlite3.connect(str(home / "state.db"))
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, title TEXT, started_at REAL, ended_at REAL,
            message_count INTEGER DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
            last_activity_at REAL, last_activity_description TEXT)
    """)
    for row in rows:
        conn.execute(
            "INSERT INTO sessions (id, title, started_at, ended_at, message_count, archived, "
            "last_activity_at, last_activity_description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row.get("title"), row.get("started_at"), row.get("ended_at"),
             row.get("message_count", 1), row.get("archived", 0),
             row.get("last_activity_at"), row.get("last_activity_description")))
    conn.commit()
    conn.close()


def _write_cron_jobs(home: Path, jobs: list[dict[str, Any]]) -> None:
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")


def _fake_module(name: str, **attrs: Any) -> ModuleType:
    mod = ModuleType(name)
    mod._PLUGIN_CTX = object()  # a registered copy, by the integration's rule
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


@pytest.fixture(autouse=True)
def no_sibling_plugins():
    """The optional integrations read sys.modules first; make sure no stale
    registered copy of either sibling plugin leaks between tests (the sibling
    test modules register them under the same module names)."""
    saved = {name: sys.modules.pop(name)
             for name in ("command_room_plugin", "group_rooms_plugin", "command_room_group_rooms")
             if name in sys.modules}
    yield
    sys.modules.update(saved)


# ── manifest + registration ──────────────────────────────────────────────────────────────


def test_manifest_declares_the_feed_knobs(plugin) -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "home-feed"
    assert manifest["requires_hermes"] == ">=0.22.0"
    schema = manifest["config_schema"]
    for knob in ("max_cards", "recent_tasks_limit", "feed_window_hours",
                 "include_command_room", "include_group_rooms"):
        assert knob in schema, f"config_schema is missing {knob}"
        assert schema[knob]["default"] == plugin.DEFAULTS[knob], (
            f"plugin.yaml default for {knob} drifted from the code's DEFAULTS")


def test_register_lands_the_plugin_methods(plugin, wired) -> None:
    names = [name for name in methods() if name.startswith("plugin.home_feed.")]
    assert len(names) == 8
    for surface in (
        "plugin.home_feed.feed", "plugin.home_feed.recent_tasks", "plugin.home_feed.dismiss",
        "plugin.home_feed.restore", "plugin.home_feed.pin.add", "plugin.home_feed.pin.remove",
        "plugin.home_feed.pins.list", "plugin.home_feed.config",
    ):
        assert surface in names
    from hermes_plus.rpc.registry import owns
    assert owns("plugin.home_feed.feed") is True


# ── config ───────────────────────────────────────────────────────────────────────────────


def test_schema_defaults_are_honoured(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx()  # no settings at all: every knob must fall to its schema default
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    try:
        assert plugin.config() == {
            "max_cards": 5, "recent_tasks_limit": 3, "feed_window_hours": 24,
            "include_command_room": True, "include_group_rooms": True,
        }
        assert plugin.feed()["cards"] == [] and plugin.recent_tasks()["tasks"] == []
    finally:
        plugin.close()


def test_settings_override_schema_defaults(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx({"max_cards": 2, "recent_tasks_limit": 1, "include_command_room": False})
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    try:
        config = plugin.config()
        assert config["max_cards"] == 2 and config["recent_tasks_limit"] == 1
        assert config["include_command_room"] is False

        # Two overdue cron jobs, but the cap is 2 anyway; a third one waits.
        now = time.time()
        _write_cron_jobs(tmp_path, [
            {"id": "a", "name": "older overdue", "state": "scheduled", "enabled": True,
             "next_run_at": now - 7200, "schedule_display": "hourly"},
            {"id": "b", "name": "newer overdue", "state": "scheduled", "enabled": True,
             "next_run_at": now - 3600, "schedule_display": "hourly"},
            {"id": "c", "name": "due soon", "state": "scheduled", "enabled": True,
             "next_run_at": now + 600, "schedule_display": "every 10m"},
        ])
        reply = plugin.feed()
        assert reply["total"] == 3
        assert [card["id"] for card in reply["cards"]] == ["cron:b", "cron:a"]  # newest first, capped

        # recent_tasks_limit 1: one task card even with several sessions.
        _write_state_db(tmp_path, [
            {"id": "s1", "title": "older", "started_at": now - 900, "last_activity_at": now - 900},
            {"id": "s2", "title": "newer", "started_at": now - 60, "last_activity_at": now - 60},
        ])
        tasks = plugin.recent_tasks()["tasks"]
        assert [task["id"] for task in tasks] == ["s2"]
    finally:
        plugin.close()


# ── the feed end to end over RPC ─────────────────────────────────────────────────────────


async def test_rpc_feed_orders_cron_cards(plugin, wired) -> None:
    now = time.time()
    _write_cron_jobs(wired.hermes_home, [
        {"id": "future", "name": "quiet job", "state": "scheduled", "enabled": True,
         "next_run_at": now + 86_400, "schedule_display": "daily"},
        {"id": "paused", "name": "paused job", "state": "paused", "enabled": True,
         "next_run_at": now - 86_400, "schedule_display": "daily"},
        {"id": "disabled", "name": "off job", "state": "scheduled", "enabled": False,
         "next_run_at": now - 86_400, "schedule_display": "daily"},
        {"id": "due", "name": "due soon", "state": "scheduled", "enabled": True,
         "next_run_at": now + 600, "schedule_display": "every 10m"},
        {"id": "late", "name": "overdue job", "state": "scheduled", "enabled": True,
         "next_run_at": now - 3600, "schedule_display": "hourly"},
    ])

    reply = await _rpc(wired, "plugin.home_feed.feed")
    cards = reply["result"]["cards"]
    # Priority ladder: overdue before due-soon; quiet/paused/disabled jobs never show.
    assert [card["id"] for card in cards] == ["cron:late", "cron:due"]
    assert [card["kind"] for card in cards] == ["cronOverdue", "cronDue"]
    assert cards[0]["title"] == "overdue job" and cards[0]["caption"] == "hourly"

    # A slot inside the scheduler's 15-minute dispatch grace is not overdue yet.
    _write_cron_jobs(wired.hermes_home, [
        {"id": "grace", "name": "within grace", "state": "scheduled", "enabled": True,
         "next_run_at": now - 300, "schedule_display": "every 5m"}])
    reply = await _rpc(wired, "plugin.home_feed.feed")
    assert [card["id"] for card in reply["result"]["cards"]] == []  # not due-soon, not past grace


async def test_rpc_dismiss_and_restore(plugin, wired) -> None:
    now = time.time()
    _write_cron_jobs(wired.hermes_home, [
        {"id": "late", "name": "overdue job", "state": "scheduled", "enabled": True,
         "next_run_at": now - 3600, "schedule_display": "hourly"}])
    assert [card["id"] for card in plugin.feed()["cards"]] == ["cron:late"]

    dismissed = await _rpc(wired, "plugin.home_feed.dismiss", {"id": "cron:late"})
    assert dismissed["result"]["card"]["dismissed"] is True
    assert plugin.feed()["cards"] == []

    restored = await _rpc(wired, "plugin.home_feed.restore", {"id": "cron:late"})
    assert restored["result"]["card"]["dismissed"] is False
    assert [card["id"] for card in plugin.feed()["cards"]] == ["cron:late"]

    fault = await _rpc(wired, "plugin.home_feed.restore", {"id": "cron:late"})
    assert fault["error"]["code"] == CARD_NOT_FOUND


async def test_rpc_pin_floats_a_card(plugin, wired) -> None:
    now = time.time()
    _write_cron_jobs(wired.hermes_home, [
        {"id": "late", "name": "overdue job", "state": "scheduled", "enabled": True,
         "next_run_at": now - 3600, "schedule_display": "hourly"},
        {"id": "due", "name": "due soon", "state": "scheduled", "enabled": True,
         "next_run_at": now + 600, "schedule_display": "every 10m"},
    ])
    assert [card["id"] for card in plugin.feed()["cards"]] == ["cron:late", "cron:due"]

    pinned = await _rpc(wired, "plugin.home_feed.pin.add", {"id": "cron:due", "title": "due soon"})
    assert pinned["result"]["pin"]["pinned"] is True
    cards = plugin.feed()["cards"]
    assert [card["id"] for card in cards] == ["cron:due", "cron:late"]
    assert cards[0]["pinned"] is True and cards[1]["pinned"] is False

    listing = await _rpc(wired, "plugin.home_feed.pins.list")
    assert [pin["id"] for pin in listing["result"]["pins"]] == ["cron:due"]

    await _rpc(wired, "plugin.home_feed.pin.remove", {"id": "cron:due"})
    assert [card["id"] for card in plugin.feed()["cards"]] == ["cron:late", "cron:due"]

    fault = await _rpc(wired, "plugin.home_feed.pin.remove", {"id": "cron:due"})
    assert fault["error"]["code"] == CARD_NOT_FOUND


# ── session cards (state.db, read-only) ──────────────────────────────────────────────────


async def test_recent_tasks_read_state_db_read_only(plugin, wired) -> None:
    now = time.time()
    _write_state_db(wired.hermes_home, [
        {"id": "old", "title": "yesterday's task", "started_at": now - 86_400,
         "last_activity_at": now - 86_400, "last_activity_description": "wrote the brief"},
        {"id": "new", "title": "just now", "started_at": now - 60, "last_activity_at": now - 60},
        {"id": "arch", "title": "archived", "started_at": now - 10, "archived": 1,
         "last_activity_at": now - 10},
        {"id": "empty", "title": "no messages", "started_at": now - 10, "message_count": 0,
         "last_activity_at": now - 10},
    ])

    reply = await _rpc(wired, "plugin.home_feed.recent_tasks")
    tasks = reply["result"]["tasks"]
    assert [task["id"] for task in tasks] == ["new", "old"]  # archived and empty rows stay out
    assert tasks[0]["title"] == "just now"
    assert tasks[1]["caption"] == "wrote the brief"

    # The read must not have touched the file (no -wal/-shm leftovers from the feed).
    assert not (wired.hermes_home / "state.db-wal").exists()


def test_recent_tasks_degrade_without_state_db(plugin, wired) -> None:
    assert plugin.recent_tasks()["tasks"] == []


# ── optional integrations: command-room / group-rooms ────────────────────────────────────


async def test_command_room_escalations_become_cards(plugin, wired) -> None:
    now = time.time()
    cr = _fake_module(
        "command_room_plugin",
        escalation_list=lambda *a, **k: [
            {"id": 1, "room": "ops", "thread_id": 3, "kind": "stalled", "title": "deploy blocked",
             "detail": "atlas waited 25m", "status": "open", "snoozed_until": None, "created_at": now - 60},
            {"id": 2, "room": "ops", "thread_id": None, "kind": "loop", "title": "acked one",
             "detail": "", "status": "acked", "snoozed_until": None, "created_at": now - 60},
            {"id": 3, "room": "ops", "thread_id": None, "kind": "stalled", "title": "snoozed one",
             "detail": "", "status": "open", "snoozed_until": now + 3_600, "created_at": now - 60},
            {"id": 4, "room": "ops", "thread_id": None, "kind": "stalled", "title": "ancient one",
             "detail": "", "status": "open", "snoozed_until": None, "created_at": now - 48 * 3_600},
        ])
    try:
        reply = await _rpc(wired, "plugin.home_feed.feed")
        cards = reply["result"]["cards"]
        # Only the open, un-snoozed, in-window escalation becomes a card.
        assert [card["id"] for card in cards] == ["escalation:ops:1"]
        assert cards[0]["kind"] == "approval" and cards[0]["title"] == "deploy blocked"
    finally:
        sys.modules.pop("command_room_plugin", None)
        del cr

    # Absent → the same feed simply loses the card, never erroring.
    reply = await _rpc(wired, "plugin.home_feed.feed")
    assert reply["result"]["cards"] == []


async def test_include_command_room_off_ignores_a_registered_command_room(
        plugin, tmp_path: Path) -> None:
    ctx = FakeCtx({"include_command_room": False})
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    _fake_module("command_room_plugin", escalation_list=lambda *a, **k: [
        {"id": 1, "room": "ops", "title": "deploy blocked", "detail": "", "status": "open",
         "snoozed_until": None, "created_at": time.time()}])
    try:
        assert plugin.feed()["cards"] == []
    finally:
        plugin.close()
        sys.modules.pop("command_room_plugin", None)


async def test_group_rooms_open_handoffs_become_cards(plugin, wired) -> None:
    gr = _fake_module(
        "group_rooms_plugin",
        list_rooms=lambda: [{"name": "ops"}, {"name": "port"}],
        room_state=lambda room: {
            "room": {"name": room}, "seats": [], "messages": [],
            "threads": [{"id": 3, "title": "deploy blocked", "created_at": time.time()}],
            "handoffs": [
                {"id": 1, "thread_id": 3, "from_bot": "atlas", "to_bot": "vex",
                 "note": "needs a review", "open": 1, "created_at": time.time() - 30},
            ] if room == "ops" else [],
        })
    try:
        reply = await _rpc(wired, "plugin.home_feed.feed")
        cards = reply["result"]["cards"]
        assert [card["id"] for card in cards] == ["handoff:ops:1"]
        assert cards[0]["kind"] == "handoff"
        assert cards[0]["title"] == "deploy blocked"
        assert "atlas → vex" in cards[0]["caption"] and "needs a review" in cards[0]["caption"]
    finally:
        sys.modules.pop("group_rooms_plugin", None)
        del gr


# ── param validation ─────────────────────────────────────────────────────────────────────


async def test_param_validation_is_invalid_params(plugin, wired) -> None:
    for method, params in (
        ("plugin.home_feed.dismiss", {}),
        ("plugin.home_feed.restore", {"id": ""}),
        ("plugin.home_feed.pin.add", {}),
        ("plugin.home_feed.pin.remove", {}),
    ):
        reply = await _rpc(wired, method, params)
        assert reply["error"]["code"] == INVALID_PARAMS, f"{method} must refuse bad params"

    reply = await _rpc(wired, "plugin.home_feed.feed", {"limit": "soon"})
    assert reply["error"]["code"] == INVALID_PARAMS
