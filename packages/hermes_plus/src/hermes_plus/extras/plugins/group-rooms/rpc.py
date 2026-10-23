"""group-rooms RPC surface: the room API as JSON-RPC methods the plugin registers itself.

Every method lives under the ``plugin.group_rooms.`` namespace and is registered
through the sidecar registry's sanctioned plugin hook (``register_plugin_method``)
when the plugin's ``register(ctx)`` runs — no core handler module lists these
methods, and ``owns()`` claims them only once they are registered. Handlers take
the same ``(ctx, params)`` contract as core handlers: params are validated, results
are the store's JSON-safe dicts, and domain failures become RpcFaults with this
plugin's own codes (missing params use the protocol's INVALID_PARAMS).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from hermes_plus.rpc.errors import INVALID_PARAMS, RpcFault

from .rooms import RoomError, RoomNotFound

logger = logging.getLogger("hermes_plus.plugins.group-rooms")

PLUGIN_ID = "group-rooms"
# Method namespace (underscores, matching the repo's other RPC namespaces);
# PLUGIN_ID keeps the manifest's slug for logging only.
_PREFIX = "plugin.group_rooms."

# Plugin-domain fault codes (the protocol's INVALID_PARAMS covers malformed params).
ROOM_NOT_FOUND = 4200
ROOM_REFUSED = 4201

Handler = Callable[[Any, dict[str, Any]], Any]


def _text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    return "" if value is None else str(value).strip()


def _required(params: dict[str, Any], key: str) -> str:
    value = _text(params, key)
    if not value:
        raise RpcFault(INVALID_PARAMS, f"{key} required")
    return value


def _optional_text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    return "" if value is None else str(value)


def _int(params: dict[str, Any], key: str) -> int:
    try:
        return int(params[key])
    except (KeyError, TypeError, ValueError) as err:
        raise RpcFault(INVALID_PARAMS, f"{key} must be an integer") from err


def _optional_int(params: dict[str, Any], key: str) -> int | None:
    if params.get(key) is None:
        return None
    return _int(params, key)


def _plugin() -> Any:
    """The loaded plugin module (this package's ``__init__``), late-bound so the
    handlers always read the CURRENT registration's knobs and store — the module
    re-points both on a fresh register(ctx)."""
    import sys

    return sys.modules[__package__]


def _domain(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one room-API call with the plugin's fault mapping (an unexpected store
    crash stays INTERNAL by dispatch; everything the store raises on purpose lands
    here)."""
    try:
        return fn(*args, **kwargs)
    except RoomNotFound as err:
        raise RpcFault(ROOM_NOT_FOUND, str(err)) from err
    except RoomError as err:
        raise RpcFault(ROOM_REFUSED, str(err)) from err


# ── handlers (ctx is the RPC context; the plugin keeps its own state) ──────────


def rooms_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"rooms": _domain(_plugin().list_rooms)}


def rooms_create(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"room": _domain(
        _plugin().create_room,
        _required(params, "name"),
        mode=_optional_text(params, "mode") or None,
        goal=_optional_text(params, "goal"),
        topic=_optional_text(params, "topic"),
    )}


def rooms_state(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"state": _domain(_plugin().room_state, _required(params, "room"))}


def seat_add(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"seat": _domain(
        _plugin().add_seat,
        _required(params, "room"),
        _required(params, "bot"),
        role=_optional_text(params, "role") or "member",
    )}


def seat_remove(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    room = _required(params, "room")
    bot = _required(params, "bot")
    return {"removed": bool(_domain(_plugin().remove_seat, room, bot)), "room": room, "bot": bot}


def seat_status(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"seat": _domain(
        _plugin().set_seat_status,
        _required(params, "room"),
        _required(params, "bot"),
        _required(params, "status"),
        note=_optional_text(params, "note"),
    )}


def message_post(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"message": _domain(
        _plugin().post_message,
        _required(params, "room"),
        _required(params, "sender"),
        _required(params, "text"),
        thread_id=_optional_int(params, "thread_id"),
    )}


def thread_start(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"thread": _domain(
        _plugin().start_thread,
        _required(params, "room"),
        title=_optional_text(params, "title"),
        origin_message_id=_optional_int(params, "origin_message_id"),
    )}


def handoff_create(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"handoff": _domain(
        _plugin().handoff,
        _required(params, "room"),
        _int(params, "thread_id"),
        _required(params, "from_bot"),
        _required(params, "to_bot"),
        note=_optional_text(params, "note"),
    )}


def handoff_resolve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"handoff": _domain(
        _plugin().resolve_handoff,
        _required(params, "room"),
        _int(params, "handoff_id"),
    )}


def config(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The effective UI-relevant knobs (the UI gate reads thread_rail_enabled)."""
    plugin = _plugin()
    return {
        "thread_rail_enabled": bool(plugin._knob("thread_rail_enabled")),
        "default_room_mode": str(plugin._knob("default_room_mode")),
        "max_seats": int(plugin._knob("max_seats")),
        "max_message_chars": int(plugin._knob("max_message_chars")),
    }


_HANDLERS: dict[str, Handler] = {
    "rooms.list": rooms_list,
    "rooms.create": rooms_create,
    "rooms.state": rooms_state,
    "seat.add": seat_add,
    "seat.remove": seat_remove,
    "seat.status": seat_status,
    "message.post": message_post,
    "thread.start": thread_start,
    "handoff.create": handoff_create,
    "handoff.resolve": handoff_resolve,
    "config": config,
}


def _registry() -> Any:
    """The sidecar's rpc registry, or None when the plugin loaded somewhere
    without hermes_plus (the room API stays loadable; the RPC door stays shut)."""
    try:
        from hermes_plus.rpc import registry
    except Exception:
        logger.debug("group-rooms: hermes_plus.rpc.registry unavailable; RPC not registered")
        return None
    return registry


def register_rpc() -> list[str]:
    """Register every plugin method through the sanctioned registry hook.

    Idempotent: a fresh registration first drops the plugin's previous methods
    (host re-register / plugin reload). Returns the registered method names;
    empty when the registry is unavailable.
    """
    registry = _registry()
    if registry is None:
        return []
    registry.unregister_plugin_methods(_PREFIX)
    for name, handler in _HANDLERS.items():
        registry.register_plugin_method(_PREFIX + name, handler)
    return [f"{_PREFIX}{name}" for name in _HANDLERS]


def unregister_rpc() -> int:
    """Drop the plugin's RPC methods (tests and plugin unload); 0 when none."""
    registry = _registry()
    if registry is None:
        return 0
    return registry.unregister_plugin_methods(_PREFIX)
