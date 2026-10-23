"""Activity plugin proof: the plugin loads through register() with a fake ctx,
honours its config_schema defaults (and explicit settings), and runs the
activity pane end to end over the registered RPC — rows sourced read-only from
the profile's state.db sessions and cron jobs.json, escalations from the
registered command-room plugin and open handoffs from group-rooms when those
are present (and nothing when they are absent).

The timestamp contract gets its own proofs: every RPC ``last_active`` is an
integer UNIX SECONDS value, millisecond source stamps are normalized down to
seconds, and 0/None stamps are omitted (a row is never shown as epoch 0 —
the fork's "20,728 days ago" bug fixed at the source)."""

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
PLUGIN_DIR = BASE / "activity"

INVALID_PARAMS = -32602


def _load_plugin(name: str, plugin_dir: Path):
    """Load a plugin the way a host would: as a standalone directory module."""
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # so `from .rpc import ...` resolves mid-exec
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
    return _load_plugin("activity_plugin", PLUGIN_DIR)


@pytest.fixture
def wired(plugin, tmp_path: Path):
    """The plugin registered against a fake host: RPC methods land in the
    sidecar registry and the read-only sources live in tmp; everything
    un-registers after (sibling-plugin isolation is autouse)."""
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    yield ctx
    plugin.close()
    assert unregister_plugin_methods("plugin.activity.") == 0  # close() dropped them all


async def _rpc(ctx: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(await handle_text(ctx, json.dumps(request)))


def _write_state_db(home: Path, rows: list[dict[str, Any]]) -> None:
    """A minimal stand-in for the profile's state.db: just the sessions columns
    the pane reads. The plugin must open it read-only and never write here."""
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
             for name in ("command_room_plugin", "group_rooms_plugin")
             if name in sys.modules}
    yield
    sys.modules.update(saved)


# ── manifest + registration ──────────────────────────────────────────────────────────────


def test_manifest_declares_the_activity_knobs(plugin) -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "activity"
    assert manifest["requires_hermes"] == ">=0.22.0"
    schema = manifest["config_schema"]
    for knob in ("window_hours", "max_rows", "now_next_done_enabled", "include_cron",
                 "now_window_minutes", "include_command_room", "include_group_rooms"):
        assert knob in schema, f"config_schema is missing {knob}"
        assert schema[knob]["default"] == plugin.DEFAULTS[knob], (
            f"plugin.yaml default for {knob} drifted from the code's DEFAULTS")
    # The timestamp contract is load-bearing: it must be stated where a host
    # admin reads first, not just in the README.
    assert "UNIX SECONDS" in manifest["description"]


def test_register_lands_the_plugin_methods(plugin, wired) -> None:
    names = [name for name in methods() if name.startswith("plugin.activity.")]
    assert len(names) == 3
    for surface in ("plugin.activity.rows", "plugin.activity.rail", "plugin.activity.config"):
        assert surface in names
    from hermes_plus.rpc.registry import owns
    assert owns("plugin.activity.rows") is True


# ── config ───────────────────────────────────────────────────────────────────────────────


def test_schema_defaults_are_honoured(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx()  # no settings at all: every knob must fall to its schema default
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    try:
        assert plugin.config() == {
            "window_hours": 24, "max_rows": 20, "now_next_done_enabled": True,
            "now_window_minutes": 10, "include_cron": True,
            "include_command_room": True, "include_group_rooms": True,
        }
        assert plugin.rows()["rows"] == [] and plugin.rail()["now"] == []
        assert plugin.rail()["enabled"] is True
    finally:
        plugin.close()


def test_settings_override_schema_defaults(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx({"max_rows": 1, "window_hours": 2, "now_next_done_enabled": False})
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    try:
        config = plugin.config()
        assert config["max_rows"] == 1 and config["window_hours"] == 2
        assert config["now_next_done_enabled"] is False

        now = time.time()
        _write_state_db(tmp_path, [
            {"id": "s1", "title": "older", "started_at": now - 900, "last_activity_at": now - 900},
            {"id": "s2", "title": "newer", "started_at": now - 60, "last_activity_at": now - 60},
        ])
        reply = plugin.rows()
        assert reply["total"] == 2
        assert [row["id"] for row in reply["rows"]] == ["session:s2"]  # newest first, capped at 1

        # The rail knob off: empty buckets, the UI hides the rail entirely.
        rail = plugin.rail()
        assert rail["enabled"] is False
        assert rail["now"] == [] and rail["next"] == [] and rail["done"] == []
    finally:
        plugin.close()


# ── rows end to end over RPC ─────────────────────────────────────────────────────────────


async def test_rpc_rows_read_state_db(plugin, wired) -> None:
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "s1", "title": "recent chat", "started_at": now - 600, "last_activity_at": now - 120},
        {"id": "s2", "title": "ended chat", "started_at": now - 900, "ended_at": now - 300,
         "last_activity_at": now - 300},
    ])
    reply = await _rpc(wired, "plugin.activity.rows", {})
    assert "error" not in reply, reply
    rows = reply["result"]["rows"]
    assert [row["id"] for row in rows] == ["session:s1", "session:s2"]  # newest first
    assert rows[0]["kind"] == "session" and rows[0]["label"] == "recent chat"
    assert rows[0]["session_id"] == "s1"
    assert rows[0]["status"] == "running"  # active inside the Now window
    assert rows[1]["status"] == "success"  # ended → a Done receipt


async def test_rpc_config_echo(plugin, wired) -> None:
    reply = await _rpc(wired, "plugin.activity.config", {})
    assert reply["result"]["max_rows"] == 20
    assert reply["result"]["now_next_done_enabled"] is True


# ── the timestamp contract ───────────────────────────────────────────────────────────────


def test_last_active_is_integer_seconds(plugin, wired) -> None:
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "s1", "title": "chat", "started_at": now - 600, "last_activity_at": now - 120},
    ])
    row = plugin.rows()["rows"][0]
    assert isinstance(row["last_active"], int)
    assert row["last_active"] == now - 120
    assert isinstance(row["created_at"], int) and row["created_at"] == now - 600


def test_millisecond_stamps_are_normalized_to_seconds(plugin, wired) -> None:
    """A source that stamps in milliseconds (the fork's bug class) comes out as
    seconds — never a 1e12-sized value the UI would turn into '20,728 days'."""
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "s1", "title": "ms chat", "started_at": now - 600,
         "last_activity_at": (now - 120) * 1000},  # ms, not seconds
    ])
    row = plugin.rows()["rows"][0]
    assert row["last_active"] == now - 120
    assert abs(row["last_active"]) < 1e12  # seconds-sized, by the contract


def test_zero_and_missing_stamps_are_omitted_never_epoch_zero(plugin, wired) -> None:
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "keep", "title": "real time", "started_at": now - 600, "last_activity_at": now - 60},
        {"id": "zeroed", "title": "epoch zero", "started_at": 0, "last_activity_at": 0},
        {"id": "nulls", "title": "no stamps", "started_at": None, "last_activity_at": None},
    ])
    ids = [row["id"] for row in plugin.rows()["rows"]]
    assert ids == ["session:keep"]  # 0 and None stamps: omitted, never epoch 0


def test_iso_string_stamps_parse(plugin, tmp_path: Path) -> None:
    from datetime import datetime, timezone

    assert plugin._to_seconds(None) is None
    assert plugin._to_seconds(0) is None
    assert plugin._to_seconds(-5) is None
    assert plugin._to_seconds("not-a-time") is None
    assert plugin._to_seconds(1_760_000_000_000) == 1_760_000_000  # ms → s
    assert plugin._to_seconds(1_760_000_000) == 1_760_000_000  # seconds pass through
    expected = int(datetime(2032, 10, 9, tzinfo=timezone.utc).timestamp())
    assert plugin._to_seconds("2032-10-09T00:00:00Z") == expected


# ── ordering, window filter, caps ────────────────────────────────────────────────────────


def test_rows_order_newest_first(plugin, wired) -> None:
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "a", "title": "oldest", "started_at": now - 900, "last_activity_at": now - 900},
        {"id": "b", "title": "newest", "started_at": now - 30, "last_activity_at": now - 30},
        {"id": "c", "title": "middle", "started_at": now - 300, "last_activity_at": now - 300},
    ])
    ids = [row["id"] for row in plugin.rows()["rows"]]
    assert ids == ["session:b", "session:c", "session:a"]


def test_window_filter_drops_stale_rows(plugin, wired) -> None:
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "fresh", "title": "in the window", "started_at": now - 600, "last_activity_at": now - 600},
        {"id": "stale", "title": "three days old", "started_at": now - 3 * 86_400,
         "last_activity_at": now - 3 * 86_400},
    ])
    ids = [row["id"] for row in plugin.rows()["rows"]]
    assert ids == ["session:fresh"]  # window_hours 24: three-day-old activity ages out


# ── the Now/Next/Done rail ───────────────────────────────────────────────────────────────


def test_rail_buckets_now_next_done(plugin, wired) -> None:
    now = int(time.time())
    _write_state_db(wired.hermes_home, [
        {"id": "live", "title": "running now", "started_at": now - 120, "last_activity_at": now - 60},
        {"id": "past", "title": "finished", "started_at": now - 900, "ended_at": now - 600,
         "last_activity_at": now - 600},
    ])
    _write_cron_jobs(wired.hermes_home, [
        {"id": "late", "name": "overdue job", "state": "scheduled", "enabled": True,
         "next_run_at": now - 3600, "schedule_display": "hourly"},
        {"id": "soon", "name": "due soon", "state": "scheduled", "enabled": True,
         "next_run_at": now + 600, "schedule_display": "every 10m"},
    ])
    rail = plugin.rail()
    assert rail["enabled"] is True
    assert [row["id"] for row in rail["now"]] == ["session:live", "cron:late"]
    # Next is the scheduler's upcoming runs, soonest first; the scheduled time
    # may lie in the future by design (it IS the next run).
    assert [row["id"] for row in rail["next"]] == ["cron:soon"]
    assert rail["next"][0]["last_active"] == now + 600
    assert [row["id"] for row in rail["done"]] == ["session:past"]


def test_include_cron_off_ignores_cron(plugin, wired) -> None:
    now = int(time.time())
    ctx = FakeCtx({"include_cron": False})
    ctx.data_dir = wired.hermes_home
    ctx.hermes_home = wired.hermes_home
    plugin.register(ctx)
    try:
        _write_cron_jobs(wired.hermes_home, [
            {"id": "late", "name": "overdue job", "state": "scheduled", "enabled": True,
             "next_run_at": now - 3600},
            {"id": "soon", "name": "due soon", "state": "scheduled", "enabled": True,
             "next_run_at": now + 600},
        ])
        assert plugin.rows()["rows"] == []
        rail = plugin.rail()
        assert rail["now"] == [] and rail["next"] == [] and rail["done"] == []
    finally:
        plugin.close()


# ── optional sibling integrations (read-only, degrade to empty) ──────────────────────────


async def test_command_room_escalations_become_rows(plugin, wired) -> None:
    now = int(time.time())
    _fake_module("command_room_plugin", escalation_list=lambda: [
        {"id": 1, "room": "ops", "status": "open", "title": "deploy blocked",
         "detail": "atlas waited 25m", "created_at": now - 120, "snoozed_until": None},
        {"id": 2, "room": "ops", "status": "resolved", "title": "already handled",
         "created_at": now - 60},
        {"id": 3, "room": "ops", "status": "open", "title": "snoozed one",
         "created_at": now - 60, "snoozed_until": now + 3600},
    ])
    reply = await _rpc(wired, "plugin.activity.rows", {})
    rows = reply["result"]["rows"]
    assert [row["id"] for row in rows] == ["escalation:ops:1"]
    assert rows[0]["kind"] == "approval" and rows[0]["status"] == "running"
    assert rows[0]["session_id"] is None


async def test_group_rooms_open_handoffs_become_rows(plugin, wired) -> None:
    now = int(time.time())
    _fake_module(
        "group_rooms_plugin",
        list_rooms=lambda: [{"name": "ops"}],
        room_state=lambda room: {
            "threads": [{"id": 7, "title": "deploy review"}],
            "handoffs": [{"id": 3, "thread_id": 7, "open": True, "from_bot": "atlas",
                          "to_bot": "vex", "note": "needs eyes", "created_at": now - 300}],
        },
    )
    reply = await _rpc(wired, "plugin.activity.rows", {})
    rows = reply["result"]["rows"]
    assert [row["id"] for row in rows] == ["handoff:ops:3"]
    assert rows[0]["kind"] == "handoff" and rows[0]["status"] == "running"
    assert rows[0]["label"] == "deploy review"


async def test_param_validation_is_invalid_params(plugin, wired) -> None:
    reply = await _rpc(wired, "plugin.activity.rows", {"limit": "soon"})
    assert reply["error"]["code"] == INVALID_PARAMS
