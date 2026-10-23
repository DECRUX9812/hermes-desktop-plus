"""home-feed RPC surface: the home feed API as JSON-RPC methods the plugin registers itself.

Every method lives under the ``plugin.home_feed.`` namespace and is registered
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

from .store import CardNotFound, FeedError

logger = logging.getLogger("hermes_plus.plugins.home-feed")

PLUGIN_ID = "home-feed"
# Method namespace (underscores, matching the repo's other RPC namespaces);
# PLUGIN_ID keeps the manifest's slug for logging only.
_PREFIX = "plugin.home_feed."

# Plugin-domain fault codes (the protocol's INVALID_PARAMS covers malformed params).
CARD_NOT_FOUND = 4220
FEED_REFUSED = 4221

Handler = Callable[[Any, dict[str, Any]], Any]


def _text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    return "" if value is None else str(value).strip()


def _required(params: dict[str, Any], key: str) -> str:
    value = _text(params, key)
    if not value:
        raise RpcFault(INVALID_PARAMS, f"{key} required")
    return value


def _optional_int(params: dict[str, Any], key: str) -> int | None:
    if params.get(key) is None:
        return None
    try:
        return int(params[key])
    except (TypeError, ValueError) as err:
        raise RpcFault(INVALID_PARAMS, f"{key} must be an integer") from err


def _plugin() -> Any:
    """The loaded plugin module (this package's ``__init__``), late-bound so the
    handlers always read the CURRENT registration's knobs and store — the module
    re-points both on a fresh register(ctx)."""
    import sys

    return sys.modules[__package__]


def _domain(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one home-feed API call with the plugin's fault mapping (an unexpected
    store crash stays INTERNAL by dispatch; everything the store raises on purpose
    lands here)."""
    try:
        return fn(*args, **kwargs)
    except CardNotFound as err:
        raise RpcFault(CARD_NOT_FOUND, str(err)) from err
    except FeedError as err:
        raise RpcFault(FEED_REFUSED, str(err)) from err


# ── handlers (ctx is the RPC context; the plugin keeps its own state) ──────────


def feed(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(_plugin().feed, limit=_optional_int(params, "limit"))


def recent_tasks(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _domain(_plugin().recent_tasks, limit=_optional_int(params, "limit"))


def dismiss(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"card": _domain(_plugin().dismiss, _required(params, "id"))}


def restore(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"card": _domain(_plugin().restore, _required(params, "id"))}


def pin_add(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"pin": _domain(
        _plugin().pin_add, _required(params, "id"), title=_text(params, "title"))}


def pin_remove(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"pin": _domain(_plugin().pin_remove, _required(params, "id"))}


def pins_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"pins": _domain(_plugin().pins)}


def config(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The effective UI-relevant knobs (the UI caps its rows with them)."""
    return _plugin().config()


_HANDLERS: dict[str, Handler] = {
    "feed": feed,
    "recent_tasks": recent_tasks,
    "dismiss": dismiss,
    "restore": restore,
    "pin.add": pin_add,
    "pin.remove": pin_remove,
    "pins.list": pins_list,
    "config": config,
}


def _registry() -> Any:
    """The sidecar's rpc registry, or None when the plugin loaded somewhere
    without hermes_plus (the feed API stays loadable; the RPC door stays shut)."""
    try:
        from hermes_plus.rpc import registry
    except Exception:
        logger.debug("home-feed: hermes_plus.rpc.registry unavailable; RPC not registered")
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
