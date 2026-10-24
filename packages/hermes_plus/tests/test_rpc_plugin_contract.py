"""Contract: the extras plugins' RPC surface (`plugin.<name>.*`).

Every extras plugin registers its JSON-RPC methods through the sidecar
registry's sanctioned hook (`register_plugin_method`) at register(ctx) time.
This file pins the cross-plugin contract:

- every plugin registers ONLY under `plugin.<its_name_underscored>.*`
- methods are absent before register() and gone after close()/unregister
- plugin methods never collide with the core contracted surface (the
  registry's import-time methods — pinned by test_rpc_registry — and the
  stock gateway contract JSON)
- every method answers JSON-serialisably: `{}` params yield a result or the
  protocol's -32602 (a non-serialisable result would surface as -32603), and
  non-object params map to -32602

The stock gateway contract (packages/shared/src/gateway-contract.openrpc.json)
deliberately lists NO `plugin.*` methods: they are runtime-registered by the
plugins themselves and are not part of the stock gateway's contracted surface,
so that test asserts their absence there too.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from hermes_plus.rpc import handle_text, methods
from hermes_plus.rpc.registry import register_plugin_method, unregister_plugin_methods

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "src" / "hermes_plus" / "extras" / "plugins"
CONTRACT_JSON = Path(__file__).resolve().parents[2] / "shared" / "src" / "gateway-contract.openrpc.json"

# plugin slug -> its underscored method namespace (`plugin.<namespace>.`).
PLUGIN_NAMESPACES: dict[str, str] = {
    "group-rooms": "group_rooms",
    "command-room": "command_room",
    "home-feed": "home_feed",
    "activity": "activity",
    "vscode": "vscode",
}

_METHOD_NAME_RE = re.compile(r"^plugin\.(group_rooms|command_room|home_feed|activity|vscode)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*$")


def _contract_method_names() -> set[str]:
    contract = json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))
    return {method["name"] for method in contract["methods"]}


def _load_plugin(slug: str):
    """Load one extras plugin the way a host would: as a standalone directory
    package (the same delivery `hermes-plus install --extras <slug>` ships)."""
    spec = importlib.util.spec_from_file_location(
        f"hermes_plus_test_extras_{slug.replace('-', '_')}",
        EXTRAS_DIR / slug / "__init__.py",
        submodule_search_locations=[str(EXTRAS_DIR / slug)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # so the plugin's own `from .x import ...` resolves
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Minimal plugin host: a settings reader and a scratch data dir."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.settings: dict[str, Any] = {}

    def get_config(self, name: str, default: Any = None) -> Any:
        return self.settings.get(name, default)


@pytest.fixture(scope="module")
def contracted_surface() -> set[str]:
    """The registry's import-time surface, snapshotted before any plugin
    registers (the surface test_rpc_registry pins, plus the gateway contract)."""
    core = set(methods())
    assert core, "the registry's import-time surface must not be empty"
    return core


def _registered(prefix: str) -> list[str]:
    return [name for name in methods() if name.startswith(prefix)]


@pytest.mark.parametrize("slug", sorted(PLUGIN_NAMESPACES))
async def test_plugin_registers_only_its_own_namespace_and_unregisters(slug, tmp_path, contracted_surface):
    prefix = f"plugin.{PLUGIN_NAMESPACES[slug]}."
    plugin = _load_plugin(slug)

    # Absent before register: an uninstalled extra leaves no trace in the surface.
    assert _registered(prefix) == []

    ctx = FakeCtx(tmp_path)
    plugin.register(ctx)

    names = _registered(prefix)
    assert names, f"{slug} registered no RPC methods"
    for name in names:
        assert _METHOD_NAME_RE.match(name), f"{slug} registered a malformed method name: {name}"
    # …and none of them leaked outside its own namespace.
    outside = [n for n in methods() if n.startswith("plugin.") and not n.startswith(prefix)]
    assert not outside, f"{slug} registered methods outside its namespace: {outside}"

    plugin.close()
    assert _registered(prefix) == []


@pytest.mark.parametrize("slug", sorted(PLUGIN_NAMESPACES))
async def test_plugin_methods_never_collide_with_the_contracted_surface(slug, tmp_path, contracted_surface):
    prefix = f"plugin.{PLUGIN_NAMESPACES[slug]}."
    plugin = _load_plugin(slug)
    ctx = FakeCtx(tmp_path)
    plugin.register(ctx)
    try:
        names = _registered(prefix)
        overlap = contracted_surface & set(names)
        assert not overlap, f"{slug} collides with the core contracted surface: {overlap}"
        assert not any(name.startswith("plugin.") for name in contracted_surface)
    finally:
        plugin.close()


@pytest.mark.parametrize("slug", sorted(PLUGIN_NAMESPACES))
async def test_plugin_methods_answer_json_or_invalid_params(slug, tmp_path):
    prefix = f"plugin.{PLUGIN_NAMESPACES[slug]}."
    plugin = _load_plugin(slug)
    ctx = FakeCtx(tmp_path)
    plugin.register(ctx)
    try:
        names = _registered(prefix)
        for name in names:
            # `{}` params: either a JSON-safe result or -32602 for a missing
            # required param — never a parse/internal error or a domain fault.
            reply = json.loads(await handle_text(
                ctx, json.dumps({"jsonrpc": "2.0", "id": 1, "method": name, "params": {}})))
            if "error" in reply:
                assert reply["error"]["code"] == -32602, (name, reply)
                assert "result" not in reply
            else:
                assert "result" in reply
                json.dumps(reply["result"], allow_nan=False)  # serialisable, no orphan floats
            # Non-object params are INVALID_PARAMS, uniformly.
            reply = json.loads(await handle_text(
                ctx, json.dumps({"jsonrpc": "2.0", "id": 2, "method": name, "params": []})))
            assert reply["error"]["code"] == -32602, (name, reply)
    finally:
        plugin.close()


async def test_register_plugin_method_rejects_names_outside_the_plugin_namespace():
    """The sanctioned hook refuses everything that is not `plugin.<id>.<action>`."""
    with pytest.raises(ValueError):
        register_plugin_method("fork.memory.list", lambda ctx, params: {})
    with pytest.raises(ValueError):
        register_plugin_method("plugin", lambda ctx, params: {})
    with pytest.raises(ValueError):
        register_plugin_method("plugin.nodot", lambda ctx, params: {})


async def test_the_core_contract_json_lists_no_plugin_methods():
    """`plugin.*` methods are runtime-registered by the plugins themselves, so
    the stock gateway contract must not carry them (documented here; the
    registry's own surface is pinned to the same guarantee above)."""
    assert not [name for name in _contract_method_names() if name.startswith("plugin.")]


async def test_unregister_removes_only_its_own_prefix(tmp_path):
    """unregister_plugin_methods is scoped: one plugin's unload leaves another's
    methods (and the core surface) untouched."""
    first = _load_plugin("group-rooms")
    second = _load_plugin("vscode")
    ctx = FakeCtx(tmp_path)
    first.register(ctx)
    second.register(ctx)
    try:
        removed = unregister_plugin_methods("plugin.group_rooms.")
        assert removed > 0
        assert _registered("plugin.group_rooms.") == []
        assert _registered("plugin.vscode."), "unregistering group-rooms dropped vscode's methods"
    finally:
        second.close()
