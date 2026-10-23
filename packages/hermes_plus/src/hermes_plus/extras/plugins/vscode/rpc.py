"""VS Code RPC surface: the code-pane API as JSON-RPC methods the plugin
registers itself.

Every method lives under the ``plugin.vscode.`` namespace and is registered
through the sidecar registry's sanctioned plugin hook (``register_plugin_method``)
when the plugin's ``register(ctx)`` runs — no core handler module lists these
methods, and ``owns()`` claims them only once they are registered. Handlers take
the same ``(ctx, params)`` contract as core handlers: params are validated and
results are the plugin's JSON-safe dicts.

WORKSPACE FENCE: ``resolve`` never spawns a process and never reads outside the
configured workspace root — an explicit folder or session id that resolves
outside the fence is an INVALID_PARAMS fault, and a missing workspace comes
back as ``{ok: false}`` rather than an invented folder.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("hermes_plus.plugins.vscode")

PLUGIN_ID = "vscode"
# Method namespace (underscores, matching the repo's other RPC namespaces);
# PLUGIN_ID keeps the manifest's slug for logging only.
_PREFIX = "plugin.vscode."

Handler = Callable[[Any, dict[str, Any]], Any]


def _plugin() -> Any:
    """The loaded plugin module (this package's ``__init__``), late-bound so the
    handlers always read the CURRENT registration's ctx — the module re-points
    it on a fresh register(ctx)."""
    import sys

    return sys.modules[__package__]


# ── handlers (ctx is the RPC context; the plugin only formats URLs over paths) ───────────


def resolve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _plugin().resolve(params or {})


def config(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The effective UI-relevant knobs (the UI sizes and gates the pane with them)."""
    return _plugin().config()


_HANDLERS: dict[str, Handler] = {
    "resolve": resolve,
    "config": config,
}


def _registry() -> Any:
    """The sidecar's rpc registry, or None when the plugin loaded somewhere
    without hermes_plus (the vscode API stays loadable; the RPC door stays shut)."""
    try:
        from hermes_plus.rpc import registry
    except Exception:
        logger.debug("vscode: hermes_plus.rpc.registry unavailable; RPC not registered")
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
