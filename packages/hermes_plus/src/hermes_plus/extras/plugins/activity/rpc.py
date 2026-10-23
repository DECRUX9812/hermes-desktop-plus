"""Activity RPC surface: the activity pane API as JSON-RPC methods the plugin
registers itself.

Every method lives under the ``plugin.activity.`` namespace and is registered
through the sidecar registry's sanctioned plugin hook (``register_plugin_method``)
when the plugin's ``register(ctx)`` runs — no core handler module lists these
methods, and ``owns()`` claims them only once they are registered. Handlers take
the same ``(ctx, params)`` contract as core handlers: params are validated and
results are the plugin's JSON-safe dicts.

TIMESTAMP CONTRACT: every row's ``last_active`` is an integer UNIX SECONDS
value (source ms stamps are normalized down at the plugin boundary; rows with
no honest timestamp are omitted). The UI converts seconds to milliseconds
exactly once (``x * 1000``) when formatting relative time.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from hermes_plus.rpc.errors import INVALID_PARAMS, RpcFault

logger = logging.getLogger("hermes_plus.plugins.activity")

PLUGIN_ID = "activity"
# Method namespace (underscores, matching the repo's other RPC namespaces);
# PLUGIN_ID keeps the manifest's slug for logging only.
_PREFIX = "plugin.activity."

Handler = Callable[[Any, dict[str, Any]], Any]


def _optional_int(params: dict[str, Any], key: str) -> int | None:
    if params.get(key) is None:
        return None
    try:
        return int(params[key])
    except (TypeError, ValueError) as err:
        raise RpcFault(INVALID_PARAMS, f"{key} must be an integer") from err


def _plugin() -> Any:
    """The loaded plugin module (this package's ``__init__``), late-bound so the
    handlers always read the CURRENT registration's ctx — the module re-points
    it on a fresh register(ctx)."""
    import sys

    return sys.modules[__package__]


# ── handlers (ctx is the RPC context; the plugin is a read-only surface) ─────────────────


def rows(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _plugin().rows(limit=_optional_int(params, "limit"))


def rail(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _plugin().rail()


def config(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The effective UI-relevant knobs (the UI gates the rail with them)."""
    return _plugin().config()


_HANDLERS: dict[str, Handler] = {
    "rows": rows,
    "rail": rail,
    "config": config,
}


def _registry() -> Any:
    """The sidecar's rpc registry, or None when the plugin loaded somewhere
    without hermes_plus (the activity API stays loadable; the RPC door stays shut)."""
    try:
        from hermes_plus.rpc import registry
    except Exception:
        logger.debug("activity: hermes_plus.rpc.registry unavailable; RPC not registered")
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
