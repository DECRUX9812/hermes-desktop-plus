"""command-room RPC surface: the CEO API as JSON-RPC methods the plugin registers itself.

Every method lives under the ``plugin.command_room.`` namespace and is registered
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

from .store import BudgetExhausted, CommandError, CommandNotFound

logger = logging.getLogger("hermes_plus.plugins.command-room")

PLUGIN_ID = "command-room"
# Method namespace (underscores, matching the repo's other RPC namespaces);
# PLUGIN_ID keeps the manifest's slug for logging only.
_PREFIX = "plugin.command_room."

# Plugin-domain fault codes (the protocol's INVALID_PARAMS covers malformed params).
COMMAND_NOT_FOUND = 4210
COMMAND_REFUSED = 4211
BUDGET_BLOCKED = 4212

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


def _options(params: dict[str, Any]) -> list[str]:
    raw = params.get("options")
    if not isinstance(raw, list) or len(raw) < 2:
        raise RpcFault(INVALID_PARAMS, "options must be a list of at least two choices")
    return [str(option) for option in raw]


def _names(params: dict[str, Any], key: str) -> list[str]:
    raw = params.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RpcFault(INVALID_PARAMS, f"{key} must be a list of names")
    return [str(name) for name in raw]


def _plugin() -> Any:
    """The loaded plugin module (this package's ``__init__``), late-bound so the
    handlers always read the CURRENT registration's knobs and store — the module
    re-points both on a fresh register(ctx)."""
    import sys

    return sys.modules[__package__]


def _domain(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one command-room API call with the plugin's fault mapping (an unexpected
    store crash stays INTERNAL by dispatch; everything the store raises on purpose
    lands here)."""
    try:
        return fn(*args, **kwargs)
    except CommandNotFound as err:
        raise RpcFault(COMMAND_NOT_FOUND, str(err)) from err
    except BudgetExhausted as err:
        raise RpcFault(BUDGET_BLOCKED, str(err)) from err
    except CommandError as err:
        raise RpcFault(COMMAND_REFUSED, str(err)) from err


# ── handlers (ctx is the RPC context; the plugin keeps its own state) ──────────


def escalations_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    room = _optional_text(params, "room") or None
    return {"escalations": _domain(
        _plugin().escalation_list, room, include_acked=bool(params.get("include_acked")))}


def escalations_create(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"escalation": _domain(
        _plugin().escalation_create,
        _required(params, "room"),
        _required(params, "title"),
        kind=_optional_text(params, "kind") or "stalled",
        detail=_optional_text(params, "detail"),
        thread_id=_optional_int(params, "thread_id"),
    )}


def escalations_ack(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"escalation": _domain(
        _plugin().escalation_ack,
        _required(params, "room"),
        _int(params, "escalation_id"),
        note=_optional_text(params, "note"),
    )}


def escalations_snooze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"escalation": _domain(
        _plugin().escalation_snooze,
        _required(params, "room"),
        _int(params, "escalation_id"),
        _int(params, "minutes"),
    )}


def sync(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"escalations": _domain(
        _plugin().sync_room, _required(params, "room"))}


def board(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"board": _domain(_plugin().board, _required(params, "room"))}


def pin_add(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"pin": _domain(
        _plugin().pin_add,
        _required(params, "room"),
        _int(params, "thread_id"),
        title=_optional_text(params, "title"),
    )}


def pin_remove(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"removed": _domain(
        _plugin().pin_remove, _required(params, "room"), _int(params, "thread_id"))}


def pins_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"pins": _domain(_plugin().pins, _required(params, "room"))}


def vote_create(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"vote": _domain(
        _plugin().vote_create,
        _required(params, "room"),
        _required(params, "question"),
        _options(params),
        quorum=_optional_int(params, "quorum"),
        asked=_names(params, "asked"),
    )}


def vote_call(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The owner's call — a vote settles ONLY here, never on unanimity."""
    return {"vote": _domain(
        _plugin().vote_call,
        _required(params, "room"),
        _int(params, "vote_id"),
        _required(params, "option"),
    )}


def vote_cast(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"vote": _domain(
        _plugin().vote_cast,
        _required(params, "room"),
        _int(params, "vote_id"),
        _required(params, "voter"),
        _required(params, "option"),
    )}


def vote_tally(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"vote": _domain(
        _plugin().vote_tally, _required(params, "room"), _int(params, "vote_id"))}


def votes_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"votes": _domain(_plugin().votes, _required(params, "room"))}


def budget_set(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"budget": _domain(
        _plugin().budget_set,
        _required(params, "room"),
        _required(params, "seat"),
        _int(params, "budget"),
    )}


def budget_spend(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"seat": _domain(
        _plugin().budget_spend,
        _required(params, "room"),
        _required(params, "seat"),
        _int(params, "amount"),
        note=_optional_text(params, "note"),
    )}


def budget_status(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"status": _domain(_plugin().budget_status, _required(params, "room"))}


def alerts_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    room = _optional_text(params, "room") or None
    return {"alerts": _domain(_plugin().alerts, room)}


def search(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        _plugin().search, _required(params, "room"), _required(params, "query"),
        limit=_optional_int(params, "limit") or 20,
    )


def catchup(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        _plugin().catchup, _required(params, "room"), since=_optional_int(params, "since"))


def recap(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        _plugin().recap, _required(params, "room"), limit=_optional_int(params, "limit") or 80)


def brief(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(_plugin().brief, hours=_optional_int(params, "hours"))


def moved_threads(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(
        _plugin().moved_threads, _required(params, "room"), mark=params.get("mark") is not False)


def config(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The effective UI-relevant knobs (the UI gate reads os_alerts_enabled)."""
    return _plugin().config()


_HANDLERS: dict[str, Handler] = {
    "escalations.list": escalations_list,
    "escalations.create": escalations_create,
    "escalations.ack": escalations_ack,
    "escalations.snooze": escalations_snooze,
    "sync": sync,
    "board.get": board,
    "pin.add": pin_add,
    "pin.remove": pin_remove,
    "pins.list": pins_list,
    "vote.create": vote_create,
    "vote.call": vote_call,
    "vote.cast": vote_cast,
    "vote.tally": vote_tally,
    "votes.list": votes_list,
    "budget.set": budget_set,
    "budget.spend": budget_spend,
    "budget.status": budget_status,
    "alerts.list": alerts_list,
    "search": search,
    "catchup": catchup,
    "recap": recap,
    "brief": brief,
    "moved.threads": moved_threads,
    "config": config,
}


def _registry() -> Any:
    """The sidecar's rpc registry, or None when the plugin loaded somewhere
    without hermes_plus (the command API stays loadable; the RPC door stays shut)."""
    try:
        from hermes_plus.rpc import registry
    except Exception:
        logger.debug("command-room: hermes_plus.rpc.registry unavailable; RPC not registered")
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
