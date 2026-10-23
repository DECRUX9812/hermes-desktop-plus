"""Group Rooms: the fork's Room 2.0 architecture as an add-on plugin.

Rooms hold multiple bot seats, one composer posts into the room, threads carry the
work, routing picks who answers, and handoffs move an open thread between seats —
ported from hermes-bots group-chat-view / bot-relay (fork Room 2.0). All knobs come
from the plugin's config_schema, read ONLY through ``ctx.get_config`` (the langfuse
pattern): the ctx is captured at register time, and ``ctx.get_config`` re-reads
config.yaml on every call so edits apply without a restart. State lives in a
plugin-owned sqlite file, opened lazily on the first room call.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes_plus.plugins.group-rooms")

from .rooms import (
    ROOM_MODES,
    SEAT_STATUSES,
    RoomError,
    RoomNotFound,
    RoomStore,
    SeatLimitError,
    USER,
)

# Must mirror the defaults declared in plugin.yaml's config_schema (the manifest test
# pins the two together). ctx.get_config already resolves schema defaults when the host
# honours them; these keep the plugin correct on hosts that pass through unknown keys.
DEFAULTS: dict[str, Any] = {
    "max_seats": 6,
    "default_room_mode": "collab",
    "thread_rail_enabled": True,
    "max_message_chars": 8000,
    "debug": False,
}

_PLUGIN_CTX: Any = None
_STORE: RoomStore | None = None
_STORE_LOCK = threading.Lock()


def _setting(name: str, default: Any = None) -> Any:
    """``plugins.entries.group-rooms.settings.<name>`` through the ctx captured at
    register time, or ``default`` when register has not run (the API is inert anyway)."""
    if _PLUGIN_CTX is None:
        return default
    try:
        return _PLUGIN_CTX.get_config(name, default)
    except Exception:  # pragma: no cover - a broken host must not kill a room call
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
        return Path(data_dir) / "group-rooms" / "rooms.db"
    home = (
        getattr(_PLUGIN_CTX, "hermes_home", None)
        or os.environ.get("HERMES_HOME")
        or str(Path.home() / ".hermes")
    )
    return Path(home) / "group-rooms" / "rooms.db"


def _store() -> RoomStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = RoomStore(_db_path())
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


# ── room API (knobs applied here, storage delegated to RoomStore) ────────────────────────


def create_room(name: str, *, mode: str | None = None, goal: str = "", topic: str = "") -> dict[str, Any]:
    return _store().create_room(name, mode=mode or _knob("default_room_mode"), goal=goal, topic=topic)


def add_seat(room: str, bot: str, *, role: str = "member") -> dict[str, Any]:
    return _store().add_seat(room, bot, role=role, seat_limit=int(_knob("max_seats")))


def remove_seat(room: str, bot: str) -> bool:
    return _store().remove_seat(room, bot)


def set_seat_status(room: str, bot: str, status: str, *, note: str = "") -> dict[str, Any]:
    return _store().set_seat_status(room, bot, status, note=note)


def seat(room: str, bot: str) -> dict[str, Any]:
    return _store().seat(room, bot)


def post_message(room: str, sender: str, text: str, *, thread_id: int | None = None) -> dict[str, Any]:
    return _store().post_message(room, sender, text, thread_id=thread_id, max_chars=int(_knob("max_message_chars")))


def start_thread(room: str, *, title: str = "", origin_message_id: int | None = None) -> dict[str, Any]:
    if not _knob("thread_rail_enabled"):
        raise RoomError("thread rail is disabled (thread_rail_enabled: false)")
    return _store().start_thread(room, title=title, origin_message_id=origin_message_id)


def route_message(room: str, message_id: int, *, mode: str | None = None) -> list[dict[str, Any]]:
    return _store().route_message(room, message_id, mode=mode)


def handoff(room: str, thread_id: int, from_bot: str, to_bot: str, *, note: str = "") -> dict[str, Any]:
    return _store().handoff(room, thread_id, from_bot, to_bot, note=note)


def resolve_handoff(room: str, handoff_id: int) -> dict[str, Any]:
    return _store().resolve_handoff(room, handoff_id)


def room_state(room: str) -> dict[str, Any]:
    return _store().room_state(room)


def list_rooms() -> list[dict[str, Any]]:
    return _store().list_rooms()


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
    # without the sidecar package still loads the room API, just with no RPC door.
    try:
        from .rpc import register_rpc
    except Exception:
        logger.debug("group-rooms: rpc module unavailable; RPC surface not registered")
    else:
        try:
            register_rpc()
        except Exception:
            logger.exception("group-rooms: RPC registration failed; the room API continues without it")
    if _knob("debug"):
        logger.debug("group-rooms registered; db resolves to %s", _db_path())


__all__ = [
    "DEFAULTS", "ROOM_MODES", "SEAT_STATUSES", "USER",
    "RoomError", "RoomNotFound", "RoomStore", "SeatLimitError",
    "add_seat", "close", "create_room", "handoff", "list_rooms", "post_message",
    "register", "remove_seat", "resolve_handoff", "room_state", "route_message",
    "seat", "set_seat_status", "start_thread",
]
