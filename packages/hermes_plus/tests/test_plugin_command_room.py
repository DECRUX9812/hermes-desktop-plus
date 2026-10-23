"""Command Room plugin proof: the plugin loads through register() with a fake ctx,
honours its config_schema defaults (and explicit settings), and runs the CEO
surface end to end over the registered RPC — escalations into the board, a
consensus vote through its quorum, a blocked overspend with its alert, pins,
snoozes, search, the 24h brief window, and which threads moved (synced from the
registered group-rooms plugin's handoffs, not duplicated)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_plus.rpc import handle_text, methods
from hermes_plus.rpc.registry import unregister_plugin_methods

BASE = Path(__file__).resolve().parents[1] / "src" / "hermes_plus" / "extras" / "plugins"
PLUGIN_DIR = BASE / "command-room"

COMMAND_NOT_FOUND = 4210
COMMAND_REFUSED = 4211
BUDGET_BLOCKED = 4212
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
    return _load_plugin("command_room_plugin", PLUGIN_DIR)


@pytest.fixture
def wired(plugin, tmp_path: Path):
    """The plugin registered against a fake host: RPC methods land in the
    sidecar registry, the store lives in tmp; everything un-registers after."""
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    yield ctx
    plugin.close()
    assert unregister_plugin_methods("plugin.command_room.") == 0  # close() dropped them all


async def _rpc(ctx: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(await handle_text(ctx, json.dumps(request)))


def test_manifest_declares_the_command_knobs(plugin) -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "command-room"
    assert manifest["requires_hermes"] == ">=0.22.0"
    schema = manifest["config_schema"]
    for knob in ("brief_window_hours", "default_seat_budget", "escalation_threshold_minutes",
                 "loop_handoffs", "consensus_quorum", "os_alerts_enabled"):
        assert knob in schema, f"config_schema is missing {knob}"
        assert schema[knob]["default"] == plugin.DEFAULTS[knob], (
            f"plugin.yaml default for {knob} drifted from the code's DEFAULTS")


def test_register_lands_the_plugin_methods(plugin, wired) -> None:
    names = [name for name in methods() if name.startswith("plugin.command_room.")]
    assert len(names) == 23
    for surface in (
        "plugin.command_room.escalations.create", "plugin.command_room.escalations.ack",
        "plugin.command_room.escalations.list", "plugin.command_room.escalations.snooze",
        "plugin.command_room.sync", "plugin.command_room.board.get",
        "plugin.command_room.pin.add", "plugin.command_room.pin.remove", "plugin.command_room.pins.list",
        "plugin.command_room.vote.create", "plugin.command_room.vote.cast", "plugin.command_room.vote.tally",
        "plugin.command_room.votes.list", "plugin.command_room.budget.set", "plugin.command_room.budget.spend",
        "plugin.command_room.budget.status", "plugin.command_room.alerts.list", "plugin.command_room.search",
        "plugin.command_room.catchup", "plugin.command_room.recap", "plugin.command_room.brief",
        "plugin.command_room.moved.threads", "plugin.command_room.config",
    ):
        assert surface in names
    from hermes_plus.rpc.registry import owns
    assert owns("plugin.command_room.board.get") is True


def test_schema_defaults_are_honoured(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx()  # no settings at all: every knob must fall to its schema default
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    try:
        assert plugin.config() == {
            "brief_window_hours": 24, "default_seat_budget": 100,
            "escalation_threshold_minutes": 20, "consensus_quorum": 2, "os_alerts_enabled": True,
        }
        # quorum default 2: one ballot leads but can't settle, two agree.
        vote = plugin.vote_create("ops", "ship or hold?", ["ship", "hold"])
        assert vote["consensus"] == "waiting"
        vote = plugin.vote_cast("ops", vote["id"], "atlas", "ship")
        assert vote["quorum_met"] is False and vote["status"] == "open"
        # default_seat_budget 100: 100 fits, 101 is blocked.
        assert plugin.budget_spend("ops", "atlas", 100)["spent"] == 100
        with pytest.raises(plugin.BudgetExhausted):
            plugin.budget_spend("ops", "atlas", 1)
    finally:
        plugin.close()


def test_settings_override_schema_defaults(plugin, tmp_path: Path) -> None:
    ctx = FakeCtx({"brief_window_hours": 6, "consensus_quorum": 3, "default_seat_budget": 5})
    ctx.data_dir = tmp_path
    plugin.register(ctx)
    try:
        config = plugin.config()
        assert config["brief_window_hours"] == 6 and config["consensus_quorum"] == 3
        assert config["default_seat_budget"] == 5

        # quorum 3: two agreeing ballots can't settle it yet...
        vote = plugin.vote_create("ops", "merge now?", ["yes", "no"])
        for voter in ("atlas", "vex"):
            vote = plugin.vote_cast("ops", vote["id"], voter, "yes")
            assert vote["status"] == "open" and vote["quorum_met"] is False
        # ...and the third unanimous ballot does.
        vote = plugin.vote_cast("ops", vote["id"], "third", "yes")
        assert vote["status"] == "decided" and vote["decided"] == "yes" and vote["consensus"] == "unanimous"

        # budget 5 default: 5 fits, 1 more is blocked.
        plugin.budget_spend("ops", "vex", 5)
        with pytest.raises(plugin.BudgetExhausted):
            plugin.budget_spend("ops", "vex", 1)

        # brief window 6h by default; an explicit 1h anchor sits 5 hours later.
        assert plugin.brief()["hours"] == 6
        assert plugin.brief(hours=1)["since"] == pytest.approx(plugin.brief()["since"] + 5 * 3_600, abs=5)
    finally:
        plugin.close()


async def test_rpc_escalation_reaches_the_board(plugin, wired) -> None:
    created = await _rpc(wired, "plugin.command_room.escalations.create",
                         {"room": "ops", "title": "deploy blocked", "kind": "stalled",
                          "detail": "atlas waited 25m", "thread_id": 3})
    escalation = created["result"]["escalation"]
    assert escalation["status"] == "open" and escalation["kind"] == "stalled"

    # The board's Needs-you column carries it (on a synthetic card: no group-rooms
    # room backs thread 3 here).
    board = (await _rpc(wired, "plugin.command_room.board.get", {"room": "ops"}))["result"]["board"]
    assert board["tiles"]["needs_you"] == 1
    assert [card["thread_id"] for card in board["columns"]["needs"]] == [3]
    assert board["columns"]["needs"][0]["title"] == "deploy blocked"

    # Ack takes it off the board.
    acked = (await _rpc(wired, "plugin.command_room.escalations.ack",
                        {"room": "ops", "escalation_id": escalation["id"], "note": "on it"}))["result"]["escalation"]
    assert acked["status"] == "acked" and acked["ack_note"] == "on it"
    board = (await _rpc(wired, "plugin.command_room.board.get", {"room": "ops"}))["result"]["board"]
    assert board["tiles"]["needs_you"] == 0 and board["columns"]["needs"] == []


async def test_rpc_vote_quorum_tally(plugin, wired) -> None:
    vote = (await _rpc(wired, "plugin.command_room.vote.create",
                       {"room": "ops", "question": "ship friday?", "options": ["ship", "wait"]}))["result"]["vote"]
    assert vote["quorum"] == 2  # consensus_quorum default

    vote = (await _rpc(wired, "plugin.command_room.vote.cast",
                       {"room": "ops", "vote_id": vote["id"], "voter": "atlas", "option": "ship"}))["result"]["vote"]
    assert vote["counts"] == {"ship": 1, "wait": 0} and vote["quorum_met"] is False

    # An unknown option is refused with the plugin's own code.
    reply = await _rpc(wired, "plugin.command_room.vote.cast",
                       {"room": "ops", "vote_id": vote["id"], "voter": "vex", "option": "maybe"})
    assert reply["error"]["code"] == COMMAND_REFUSED

    vote = (await _rpc(wired, "plugin.command_room.vote.cast",
                       {"room": "ops", "vote_id": vote["id"], "voter": "vex", "option": "wait"}))["result"]["vote"]
    # 1-1 past two voters: a split, not a decision.
    assert vote["consensus"] == "split" and vote["status"] == "open"

    # Recasts win (last pick, like the fork's tally): atlas flips to ship, vex joins.
    vote = (await _rpc(wired, "plugin.command_room.vote.cast",
                       {"room": "ops", "vote_id": vote["id"], "voter": "atlas", "option": "ship"}))["result"]["vote"]
    assert vote["counts"] == {"ship": 1, "wait": 1}
    vote = (await _rpc(wired, "plugin.command_room.vote.cast",
                       {"room": "ops", "vote_id": vote["id"], "voter": "vex", "option": "ship"}))["result"]["vote"]
    assert vote["status"] == "decided" and vote["decided"] == "ship"
    tally = (await _rpc(wired, "plugin.command_room.vote.tally",
                        {"room": "ops", "vote_id": vote["id"]}))["result"]["vote"]
    assert tally["voters"] == 2 and tally["quorum_met"] is True


async def test_rpc_budget_overspend_blocked_and_alerted(plugin, wired) -> None:
    assert (await _rpc(wired, "plugin.command_room.budget.set",
                       {"room": "ops", "seat": "atlas", "budget": 10}))["result"]["budget"]["budget"] == 10

    spent = (await _rpc(wired, "plugin.command_room.budget.spend",
                        {"room": "ops", "seat": "atlas", "amount": 8, "note": "tool calls"}))["result"]["seat"]
    assert spent["spent"] == 8 and spent["remaining"] == 2

    blocked = await _rpc(wired, "plugin.command_room.budget.spend",
                         {"room": "ops", "seat": "atlas", "amount": 3})
    assert blocked["error"]["code"] == BUDGET_BLOCKED
    assert "budget" in blocked["error"]["message"]

    # The blocked spend recorded nothing AND raised the OS alert.
    status = (await _rpc(wired, "plugin.command_room.budget.status", {"room": "ops"}))["result"]["status"]
    assert status["seats"] == [{"seat": "atlas", "budget": 10, "spent": 8, "remaining": 2}]
    alerts = (await _rpc(wired, "plugin.command_room.alerts.list", {"room": "ops"}))["result"]["alerts"]
    assert len(alerts) == 1 and alerts[0]["kind"] == "budget" and "atlas" in alerts[0]["message"]

    # os_alerts_enabled: false still blocks, but raises no alert.
    quiet_ctx = FakeCtx({"os_alerts_enabled": False, "db_path": str(wired.data_dir / "quiet.db")})
    plugin.register(quiet_ctx)
    try:
        await _rpc(quiet_ctx, "plugin.command_room.budget.set", {"room": "ops", "seat": "vex", "budget": 1})
        blocked = await _rpc(quiet_ctx, "plugin.command_room.budget.spend",
                             {"room": "ops", "seat": "vex", "amount": 2})
        assert blocked["error"]["code"] == BUDGET_BLOCKED
        assert (await _rpc(quiet_ctx, "plugin.command_room.alerts.list", {}))["result"]["alerts"] == []
    finally:
        plugin.close()


async def test_rpc_pin_snooze_search(plugin, wired) -> None:
    assert (await _rpc(wired, "plugin.command_room.pin.add",
                       {"room": "ops", "thread_id": 7, "title": "the launch"}))["result"]["pin"]["pinned"] is True
    assert [pin["thread_id"] for pin in
            (await _rpc(wired, "plugin.command_room.pins.list", {"room": "ops"}))["result"]["pins"]] == [7]
    assert (await _rpc(wired, "plugin.command_room.pin.remove",
                       {"room": "ops", "thread_id": 7}))["result"]["removed"] is True
    assert (await _rpc(wired, "plugin.command_room.pins.list", {"room": "ops"}))["result"]["pins"] == []

    # Snooze keeps an escalation open but hides it from the board until it lifts.
    esc = (await _rpc(wired, "plugin.command_room.escalations.create",
                      {"room": "ops", "title": "waiting on vex"}))["result"]["escalation"]
    snoozed = (await _rpc(wired, "plugin.command_room.escalations.snooze",
                          {"room": "ops", "escalation_id": esc["id"], "minutes": 30}))["result"]["escalation"]
    assert snoozed["snoozed_until"] is not None and snoozed["status"] == "open"
    board = (await _rpc(wired, "plugin.command_room.board.get", {"room": "ops"}))["result"]["board"]
    assert board["tiles"]["needs_you"] == 0
    assert len((await _rpc(wired, "plugin.command_room.escalations.list", {"room": "ops"}))["result"]["escalations"]) == 1

    # Search over the room's log: with no group-rooms room registered, an empty
    # well-formed result (the integration case is covered below).
    found = (await _rpc(wired, "plugin.command_room.search",
                        {"room": "ops", "query": "launch"}))["result"]
    assert found["room"] == "ops" and found["messages"] == []


async def test_rpc_brief_window(plugin, wired) -> None:
    await _rpc(wired, "plugin.command_room.escalations.create", {"room": "ops", "title": "deploy blocked"})
    result = (await _rpc(wired, "plugin.command_room.brief", {}))["result"]
    assert result["hours"] == 24  # the 24h brief default
    ops = next(room for room in result["rooms"] if room["room"] == "ops")
    assert ops["escalations"] == 1 and ops["escalations_open"] == 1

    # An explicit window moves the anchor: 1h reaches back further than 24h's since.
    tight = (await _rpc(wired, "plugin.command_room.brief", {"hours": 1}))["result"]
    assert tight["hours"] == 1 and tight["since"] > result["since"]


async def test_moved_threads_marks_forward_and_syncs_group_rooms_handoffs(plugin, tmp_path: Path) -> None:
    # The group-rooms plugin registered first: command-room reads its rooms and
    # handoffs through the public API instead of duplicating them.
    group_rooms = _load_plugin("group_rooms_plugin", BASE / "group-rooms")
    gr_ctx = FakeCtx()
    gr_ctx.data_dir = tmp_path / "gr"
    group_rooms.register(gr_ctx)
    try:
        group_rooms.create_room("ops", goal="the command room integration")
        group_rooms.add_seat("ops", "atlas", role="lead")
        group_rooms.add_seat("ops", "vex")
        thread = group_rooms.start_thread("ops", title="the launch")
        group_rooms.post_message("ops", "user", "make the launch page pop", thread_id=thread["id"])
        group_rooms.handoff("ops", thread["id"], "atlas", "vex", note="hero shot")

        ctx = FakeCtx()
        ctx.data_dir = tmp_path / "cr"
        plugin.register(ctx)
        try:
            # First poll: the thread moved.
            moved = (await _rpc(ctx, "plugin.command_room.moved.threads", {"room": "ops"}))["result"]
            assert [t["thread_id"] for t in moved["threads"]] == [thread["id"]]
            assert moved["threads"][0]["title"] == "the launch"
            # Second poll marks forward: nothing moved again since the mark.
            moved = (await _rpc(ctx, "plugin.command_room.moved.threads", {"room": "ops"}))["result"]
            assert moved["threads"] == []

            # The handoff is open and fresh: the sync raises no escalation yet.
            fresh = (await _rpc(ctx, "plugin.command_room.sync", {"room": "ops"}))["result"]["escalations"]
            assert fresh == []

            # Once the threshold passes, the sync escalates the stalled handoff —
            # deduped, so a second sync does not duplicate it.
            ctx.settings["escalation_threshold_minutes"] = -1  # everything is stale now
            stalled = (await _rpc(ctx, "plugin.command_room.sync", {"room": "ops"}))["result"]["escalations"]
            assert len(stalled) == 1 and stalled[0]["kind"] == "stalled"
            again = (await _rpc(ctx, "plugin.command_room.sync", {"room": "ops"}))["result"]["escalations"]
            assert [esc["id"] for esc in again] == [esc["id"] for esc in stalled]
            board = (await _rpc(ctx, "plugin.command_room.board.get", {"room": "ops"}))["result"]["board"]
            assert board["tiles"]["needs_you"] == 1

            # Search reaches the group-rooms log through the same API.
            found = (await _rpc(ctx, "plugin.command_room.search",
                                {"room": "ops", "query": "launch page"}))["result"]
            assert len(found["messages"]) == 1 and "pop" in found["messages"][0]["text"]

            # Catch-up lines the launch thread up with its escalation.
            catchup = (await _rpc(ctx, "plugin.command_room.catchup", {"room": "ops"}))["result"]
            assert catchup["lines"] and catchup["lines"][0]["thread_id"] == thread["id"]
            assert catchup["lines"][0]["escalated"] is True

            # The brief folds the group-rooms room in with command-room's own state.
            brief = (await _rpc(ctx, "plugin.command_room.brief", {}))["result"]
            ops = next(room for room in brief["rooms"] if room["room"] == "ops")
            assert ops["fresh_messages"] == 1 and ops["escalations"] == 1
        finally:
            plugin.close()
    finally:
        group_rooms.close()
        assert unregister_plugin_methods("plugin.group_rooms.") == 0
        assert unregister_plugin_methods("plugin.command_room.") == 0


async def test_param_validation_is_invalid_params(plugin, wired) -> None:
    for surface, params in (
        ("plugin.command_room.escalations.create", {"room": "ops"}),
        ("plugin.command_room.escalations.create", {"room": "ops", "title": ""}),
        ("plugin.command_room.board.get", {}),
        ("plugin.command_room.vote.create", {"room": "ops", "question": "?", "options": ["one"]}),
        ("plugin.command_room.vote.create", {"room": "ops", "question": "?", "options": "ship"}),
        ("plugin.command_room.budget.spend", {"room": "ops", "seat": "atlas"}),
        ("plugin.command_room.search", {"room": "ops", "query": "  "}),
    ):
        reply = await _rpc(wired, surface, params)
        assert reply["error"]["code"] == INVALID_PARAMS, f"{surface} accepted {params}"
    # non-object params are refused by the protocol itself
    reply = json.loads(await handle_text(wired, json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "plugin.command_room.brief", "params": []})))
    assert reply["error"]["code"] == INVALID_PARAMS


async def test_domain_faults_use_the_plugin_codes(plugin, wired) -> None:
    ack = await _rpc(wired, "plugin.command_room.escalations.ack",
                     {"room": "ops", "escalation_id": 999})
    assert ack["error"]["code"] == COMMAND_NOT_FOUND

    bad_kind = await _rpc(wired, "plugin.command_room.escalations.create",
                          {"room": "ops", "title": "?", "kind": "panic"})
    assert bad_kind["error"]["code"] == COMMAND_REFUSED

    unknown = await _rpc(wired, "plugin.command_room.vote.tally", {"room": "ops", "vote_id": 5})
    assert unknown["error"]["code"] == COMMAND_NOT_FOUND

    # A vote from another room is not reachable through that room's neighbours.
    vote = (await _rpc(wired, "plugin.command_room.vote.create",
                       {"room": "ops", "question": "?", "options": ["a", "b"]}))["result"]["vote"]
    reply = await _rpc(wired, "plugin.command_room.vote.tally", {"room": "other", "vote_id": vote["id"]})
    assert reply["error"]["code"] == COMMAND_NOT_FOUND
