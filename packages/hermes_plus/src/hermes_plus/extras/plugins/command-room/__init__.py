"""Command Room: the fork's CEO surface as an add-on plugin — a handoff
protocol with escalations, the CEO board, catch-up/recaps/search/pins/snoozes,
the 24h brief, consensus voting cards, seat budgets with OS alerts, and the
thread rail's "which threads moved" indicator.

Ported from hermes-bots group-room-handoffs / group-room-votes / group-room-board
/ group-room-catchup (fork command room). Rooms, threads, messages and handoffs
themselves are NOT duplicated: when the group-rooms plugin is registered in this
process, command-room reads them through its public API (``list_rooms``,
``room_state``); without it, command-room still runs on its own state and the
group-rooms-backed surfaces degrade to empty. All knobs come from the plugin's
config_schema, read ONLY through ``ctx.get_config`` (the langfuse pattern): the
ctx is captured at register time, and ``ctx.get_config`` re-reads config.yaml on
every call so edits apply without a restart. State lives in a plugin-owned
sqlite file, opened lazily on the first command-room call.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes_plus.plugins.command-room")

from .store import (
    ESCALATION_KINDS,
    BudgetExhausted,
    CommandError,
    CommandNotFound,
    CommandStore,
)

# Must mirror the defaults declared in plugin.yaml's config_schema (the manifest
# test pins the two together). ctx.get_config already resolves schema defaults
# when the host honours them; these keep the plugin correct on hosts that pass
# through unknown keys.
DEFAULTS: dict[str, Any] = {
    "brief_window_hours": 24,
    "default_seat_budget": 100,
    "escalation_threshold_minutes": 20,
    "loop_handoffs": 3,
    "consensus_quorum": 2,
    "os_alerts_enabled": True,
    "debug": False,
}

_PLUGIN_CTX: Any = None
_STORE: CommandStore | None = None
_STORE_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def _setting(name: str, default: Any = None) -> Any:
    """``plugins.entries.command-room.settings.<name>`` through the ctx captured at
    register time, or ``default`` when register has not run (the API is inert anyway)."""
    if _PLUGIN_CTX is None:
        return default
    try:
        return _PLUGIN_CTX.get_config(name, default)
    except Exception:  # pragma: no cover - a broken host must not kill a command call
        return default


def _knob(name: str) -> Any:
    value = _setting(name, DEFAULTS[name])
    return DEFAULTS[name] if value is None else value


def _db_path() -> Path:
    """Where the plugin's sqlite file lives: explicit db_path setting, then the host's
    data dir when the ctx provides one, then the profile home the ctx (or env) names."""
    override = str(_setting("db_path", "") or "").strip()
    if override:
        return Path(override)
    data_dir = getattr(_PLUGIN_CTX, "data_dir", None)
    if data_dir:
        return Path(data_dir) / "command-room" / "command-room.db"
    home = (
        getattr(_PLUGIN_CTX, "hermes_home", None)
        or os.environ.get("HERMES_HOME")
        or str(Path.home() / ".hermes")
    )
    return Path(home) / "command-room" / "command-room.db"


def _store() -> CommandStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = CommandStore(_db_path())
        return _STORE


def close() -> None:
    """Close the sqlite store and drop the plugin's RPC methods (tests and host shutdown)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
            _STORE = None
    try:
        from .rpc import unregister_rpc
    except Exception:
        pass
    else:
        unregister_rpc()


# ── group-rooms integration (read-only, optional) ────────────────────────────────────────

_GR_MODULE_NAMES = ("group_rooms_plugin", "command_room_group_rooms")
_GR_SIBLING = Path(__file__).resolve().parent.parent / "group-rooms"


def _group_rooms() -> Any | None:
    """The registered group-rooms plugin module, or None.

    Only a REGISTERED copy counts (its ``_PLUGIN_CTX`` set at register time):
    calling an unregistered copy would open its store at the default profile
    home, which the plugin must never do behind the host's back. Copies the host
    or the tests already loaded are found in sys.modules; otherwise the sibling
    plugin directory loads once, the way the tests load it."""
    for name in _GR_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "_PLUGIN_CTX", None) is not None:
            return mod
    try:
        spec = importlib.util.spec_from_file_location(
            _GR_MODULE_NAMES[1], _GR_SIBLING / "__init__.py",
            submodule_search_locations=[str(_GR_SIBLING)],
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception:
        logger.debug("command-room: group-rooms plugin not loadable; running without room integration")
        return None
    if getattr(mod, "_PLUGIN_CTX", None) is None:
        return None
    return mod


def _room_state(room: str) -> dict[str, Any]:
    """group-rooms' room snapshot, or an empty shell when the integration is off."""
    plugin = _group_rooms()
    if plugin is None:
        return {"room": {"name": room}, "seats": [], "threads": [], "messages": [], "handoffs": []}
    try:
        return plugin.room_state(room)
    except Exception:
        logger.debug("command-room: group-rooms room %r unavailable", room)
        return {"room": {"name": room}, "seats": [], "threads": [], "messages": [], "handoffs": []}


# ── escalations ──────────────────────────────────────────────────────────────────────────


def escalation_create(
    room: str, title: str, *, kind: str = "stalled", detail: str = "", thread_id: int | None = None,
) -> dict[str, Any]:
    return _store().escalation_create(room, kind, title, detail=detail, thread_id=thread_id)


def escalation_ack(room: str, escalation_id: int, *, note: str = "") -> dict[str, Any]:
    return _store().escalation_ack(room, escalation_id, note=note)


def escalation_snooze(room: str, escalation_id: int, minutes: int) -> dict[str, Any]:
    return _store().escalation_snooze(room, escalation_id, minutes)


def escalation_list(room: str | None = None, *, include_acked: bool = False) -> list[dict[str, Any]]:
    return _store().escalations(room, include_acked=include_acked)


def sync_room(room: str) -> list[dict[str, Any]]:
    """Pull the room's open handoffs through the group-rooms API and raise
    escalations for the stalled ones and for seat-to-seat loops."""
    return _store().sync_handoffs(
        room, _room_state(room)["handoffs"],
        stalled_after_seconds=int(_knob("escalation_threshold_minutes")) * 60,
        loop_count=int(_knob("loop_handoffs")),
    )


# ── pins ─────────────────────────────────────────────────────────────────────────────────


def pin_add(room: str, thread_id: int, *, title: str = "") -> dict[str, Any]:
    return _store().pin_add(room, thread_id, title=title)


def pin_remove(room: str, thread_id: int) -> bool:
    return _store().pin_remove(room, thread_id)


def pins(room: str) -> list[dict[str, Any]]:
    return _store().pins(room)


# ── consensus votes ──────────────────────────────────────────────────────────────────────


def vote_create(room: str, question: str, options: list[str], *, quorum: int | None = None) -> dict[str, Any]:
    return _store().vote_create(
        room, question, options, quorum=int(quorum if quorum is not None else _knob("consensus_quorum")))


def vote_cast(room: str, vote_id: int, voter: str, option: str) -> dict[str, Any]:
    return _store().vote_cast(room, vote_id, voter, option)


def vote_tally(room: str, vote_id: int) -> dict[str, Any]:
    return _store().vote_tally(vote_id, room=room)


def votes(room: str) -> list[dict[str, Any]]:
    return _store().votes(room)


# ── seat budgets ─────────────────────────────────────────────────────────────────────────


def budget_set(room: str, seat: str, budget: int) -> dict[str, Any]:
    return _store().budget_set(room, seat, budget)


def budget_spend(room: str, seat: str, amount: int, *, note: str = "") -> dict[str, Any]:
    return _store().budget_spend(
        room, seat, amount, note=note, budget=int(_knob("default_seat_budget")),
        os_alerts=bool(_knob("os_alerts_enabled")),
    )


def budget_status(room: str) -> dict[str, Any]:
    return _store().budget_status(room, default_budget=int(_knob("default_seat_budget")))


def alerts(room: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    return _store().alerts(room, limit=limit)


# ── search ───────────────────────────────────────────────────────────────────────────────


def search(room: str, query: str, *, limit: int = 20) -> dict[str, Any]:
    """Room messages matching the query, through the group-rooms API (a plain
    LIKE over the room's log — search stays a read, never an index to build)."""
    needle = str(query or "").strip().lower()
    if not needle:
        raise CommandError("search query must not be empty")
    hits = []
    for message in _room_state(room)["messages"]:
        if needle in str(message.get("text") or "").lower():
            hits.append({
                "id": message.get("id"), "thread_id": message.get("thread_id"),
                "sender": message.get("sender"), "text": message.get("text"),
                "created_at": message.get("created_at"),
            })
            if len(hits) >= max(1, int(limit)):
                break
    return {"room": room, "query": query, "messages": hits}


# ── board, catch-up, recap, brief ────────────────────────────────────────────────────────


def _thread_activity(room: str) -> dict[int, dict[str, Any]]:
    """Per-thread last-activity + message counts from the group-rooms snapshot."""
    state = _room_state(room)
    threads = {
        int(thread["id"]): {"thread_id": int(thread["id"]), "title": str(thread.get("title") or ""),
                            "last_at": int(thread.get("created_at") or 0), "messages": 0}
        for thread in state["threads"]
    }
    for message in state["messages"]:
        thread_id = message.get("thread_id")
        if thread_id is None:
            continue
        entry = threads.setdefault(int(thread_id), {
            "thread_id": int(thread_id), "title": "", "last_at": 0, "messages": 0})
        entry["messages"] += 1
        entry["last_at"] = max(entry["last_at"], int(message.get("created_at") or 0))
    return threads


def _escalated_threads(room: str, now: int) -> dict[int | None, dict[str, Any]]:
    """Threads carrying an open, un-snoozed escalation (snoozed ones wait
    quietly), keyed by thread with the escalation as its payload."""
    return {
        esc["thread_id"]: esc for esc in escalation_list(room)
        if esc["status"] == "open" and (esc["snoozed_until"] is None or esc["snoozed_until"] <= now)
    }


def board(room: str) -> dict[str, Any]:
    """The CEO board: the room's work as columns — Needs you (escalated) ·
    Waiting (open handoffs) · Working (seats mid-thread) · Answered — with the
    at-a-glance tiles on top, matching the fork's group-room-board columns."""
    state = _room_state(room)
    now = int(_now())
    escalated = _escalated_threads(room, now)
    open_handoffs: dict[int, list[dict[str, Any]]] = {}
    for handoff in state["handoffs"]:
        if handoff.get("open"):
            open_handoffs.setdefault(int(handoff["thread_id"]), []).append(handoff)

    threads = _thread_activity(room)
    # An escalation on a thread the integration can't see still reaches the
    # board: a synthetic card carries the escalation's own title.
    for thread_id, esc in escalated.items():
        key = f"esc-{esc['id']}"
        if key not in threads and (thread_id is None or thread_id not in threads):
            threads[key] = {
                "thread_id": thread_id, "title": esc["title"],
                "last_at": int(esc["created_at"] or 0), "messages": 0,
            }
    columns: dict[str, list[dict[str, Any]]] = {"needs": [], "waiting": [], "working": [], "answered": []}
    for entry in sorted(threads.values(), key=lambda row: -row["last_at"]):
        card = {
            "thread_id": entry["thread_id"], "title": entry["title"],
            "pinned": any(pin["thread_id"] == entry["thread_id"] for pin in pins(room)),
            "escalated": entry["thread_id"] in escalated,
            "open_handoffs": len(open_handoffs.get(entry["thread_id"], [])),
            "last_at": entry["last_at"], "messages": entry["messages"],
        }
        if card["escalated"]:
            columns["needs"].append(card)
        elif card["open_handoffs"]:
            columns["waiting"].append(card)
        elif any(seat.get("status") == "working" for seat in state["seats"]) and card["messages"]:
            columns["working"].append(card)
        else:
            columns["answered"].append(card)

    day_start = now - (now % 86_400)
    return {
        "room": room,
        "tiles": {
            "needs_you": len(columns["needs"]),
            "open_handoffs": sum(len(rows) for rows in open_handoffs.values()),
            "escalations_open": len([esc for esc in escalation_list(room) if esc["status"] == "open"]),
            "delivered_today": len([h for h in state["handoffs"] if not h.get("open")
                                    and int(h.get("created_at") or 0) >= day_start]),
        },
        "columns": columns,
    }


def catchup(room: str, *, since: int | None = None) -> dict[str, Any]:
    """What happened while the CEO was away: one line per thread that moved,
    got escalated, or is pinned — freshest first (the fork's roomCatchUp)."""
    anchor = int(since) if since is not None else _now() - int(_knob("brief_window_hours")) * 3_600
    now = int(_now())
    escalated = _escalated_threads(room, now)
    activity = _thread_activity(room)
    seen = _store().seen_map(room)
    pinned = {pin["thread_id"] for pin in pins(room)}
    state = _room_state(room)

    # Escalated threads the integration can't see still get a catch-up line.
    for thread_id, esc in escalated.items():
        if thread_id is not None and thread_id in activity:
            continue
        activity[f"esc-{esc['id']}"] = {
            "thread_id": thread_id, "title": esc["title"], "last_at": int(esc["created_at"] or 0), "messages": 0}

    lines = []
    for entry in activity.values():
        fresh = sum(1 for message in state["messages"]
                    if message.get("thread_id") == entry["thread_id"]
                    and int(message.get("created_at") or 0) > anchor)
        line = {
            "thread_id": entry["thread_id"], "title": entry["title"],
            "fresh": fresh, "escalated": entry["thread_id"] in escalated,
            "pinned": entry["thread_id"] in pinned,
            "unseen": entry["last_at"] > seen.get(entry["thread_id"], 0),
        }
        if fresh or line["escalated"] or line["pinned"]:
            lines.append(line)
    lines.sort(key=lambda line: (not (line["escalated"] or line["pinned"]), -line["fresh"]))
    return {"room": room, "since": anchor, "total": sum(line["fresh"] for line in lines), "lines": lines}


def recap(room: str, *, limit: int = 80) -> dict[str, Any]:
    """The room's recent log as compact lines, shaped for a model recap (the
    fork's recapEntries input): 'Who: what', newest room first, thread-tagged."""
    lines = []
    for message in _room_state(room)["messages"][-max(1, int(limit)):]:
        who = "User (CEO)" if message.get("sender") == "user" else str(message.get("sender"))
        text = " ".join(str(message.get("text") or "").split())[:600]
        thread = message.get("thread_id")
        lines.append({"thread_id": thread, "line": f"{who}: {text}", "created_at": message.get("created_at")})
    return {"room": room, "entries": lines}


def brief(*, hours: int | None = None) -> dict[str, Any]:
    """The 24h brief across every room: new messages, escalations raised,
    votes settled, budget alerts — the one screen before the CEO starts."""
    window = int(hours if hours is not None else _knob("brief_window_hours"))
    now = int(_now())
    since = now - window * 3_600
    group_rooms = _group_rooms()
    rooms = []
    if group_rooms is not None:
        try:
            rooms = [row["name"] for row in group_rooms.list_rooms()]
        except Exception:
            rooms = []
    rooms = sorted(set(rooms) | {esc["room"] for esc in escalation_list()} | set(_store().vote_rooms()))
    out_rooms = []
    for room in rooms:
        state = _room_state(room)
        fresh = len([m for m in state["messages"] if int(m.get("created_at") or 0) > since])
        escalations = [esc for esc in escalation_list(room, include_acked=True)
                       if int(esc["created_at"]) > since]
        budget_alerts = [a for a in alerts(room) if a["kind"] == "budget" and int(a["created_at"]) > since]
        out_rooms.append({
            "room": room, "fresh_messages": fresh,
            "escalations": len(escalations),
            "escalations_open": len([esc for esc in escalations if esc["status"] == "open"]),
            "budget_alerts": len(budget_alerts),
        })
    return {"since": since, "hours": window, "rooms": out_rooms}


# ── moved threads ────────────────────────────────────────────────────────────────────────


def moved_threads(room: str, *, mark: bool = True) -> dict[str, Any]:
    """Which threads moved since the CEO last looked (the rail's per-thread
    nudge). Marking on read, like the fork's markGroupThreadsRead: a mark only
    ever moves forward, so a re-poll never un-marks a thread that moved again —
    its last_at is newer than the stored mark."""
    now = int(_now())
    seen = _store().seen_map(room)
    activity = _thread_activity(room)
    moved = [
        entry for entry in sorted(activity.values(), key=lambda row: -row["last_at"])
        if entry["last_at"] > seen.get(entry["thread_id"], 0)
    ]
    if mark:
        _store().mark_seen(room, list(activity))
    return {"room": room, "threads": moved, "marked": bool(mark)}


# ── config echo (the UI gate) ────────────────────────────────────────────────────────────


def config() -> dict[str, Any]:
    """The effective UI-relevant knobs."""
    return {
        "brief_window_hours": int(_knob("brief_window_hours")),
        "default_seat_budget": int(_knob("default_seat_budget")),
        "escalation_threshold_minutes": int(_knob("escalation_threshold_minutes")),
        "consensus_quorum": int(_knob("consensus_quorum")),
        "os_alerts_enabled": bool(_knob("os_alerts_enabled")),
    }


def register(ctx) -> None:
    global _PLUGIN_CTX, _STORE
    _PLUGIN_CTX = ctx
    # A fresh registration re-points the store: the previous ctx's db must not leak
    # into the new home (matters for tests and for host re-registration).
    if _STORE is not None:
        _STORE.close()
        _STORE = None
    # The RPC surface registers through the sidecar registry's sanctioned plugin
    # hook, here where every other feature wires up. Lazy import + guard: a host
    # without the sidecar package still loads the command API, just with no RPC door.
    try:
        from .rpc import register_rpc
    except Exception:
        logger.debug("command-room: rpc module unavailable; RPC surface not registered")
    else:
        try:
            register_rpc()
        except Exception:
            logger.exception("command-room: RPC registration failed; the command API continues without it")
    if _knob("debug"):
        logger.debug("command-room registered; db resolves to %s", _db_path())


__all__ = [
    "DEFAULTS", "ESCALATION_KINDS",
    "BudgetExhausted", "CommandError", "CommandNotFound", "CommandStore",
    "alerts", "board", "brief", "budget_set", "budget_spend", "budget_status", "catchup", "close",
    "config", "escalation_ack", "escalation_create", "escalation_list", "escalation_snooze",
    "moved_threads", "pin_add", "pin_remove", "pins", "recap", "register", "search",
    "sync_room", "vote_cast", "vote_create", "vote_tally", "votes",
]
