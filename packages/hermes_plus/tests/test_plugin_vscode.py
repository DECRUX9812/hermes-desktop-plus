"""VS Code plugin proof: the plugin loads through register() with a fake ctx,
honours its config_schema defaults (and explicit settings), and runs the code
pane end to end over the registered RPC — workspace resolution, URL encoding,
the vscode:// deep-link fallback, and the workspace fence (a folder or session
id that resolves outside workspace_root is INVALID_PARAMS; the plugin never
spawns a process and never invents a folder)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import yaml

from hermes_plus.rpc import handle_text, methods
from hermes_plus.rpc.registry import unregister_plugin_methods

BASE = Path(__file__).resolve().parents[1] / "src" / "hermes_plus" / "extras" / "plugins"
PLUGIN_DIR = BASE / "vscode"

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
    return _load_plugin("vscode_plugin", PLUGIN_DIR)


@pytest.fixture
def wired(plugin, tmp_path: Path):
    """The plugin registered against a fake host: RPC methods land in the
    sidecar registry and the profile home (the workspace-root fallback) lives
    in tmp; everything un-registers after (sibling-plugin isolation is autouse)."""
    ctx = FakeCtx()
    ctx.data_dir = tmp_path
    ctx.hermes_home = tmp_path
    plugin.register(ctx)
    yield ctx
    plugin.close()
    assert unregister_plugin_methods("plugin.vscode.") == 0  # close() dropped them all


async def _rpc(ctx: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(await handle_text(ctx, json.dumps(request)))


# ── manifest + registration ──────────────────────────────────────────────────────────────


def test_manifest_declares_the_vscode_knobs(plugin) -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "vscode"
    assert manifest["requires_hermes"] == ">=0.22.0"
    schema = manifest["config_schema"]
    for knob in ("server_url", "workspace_root", "open_side", "default_width",
                 "deep_link_enabled", "cli_pane_enabled"):
        assert knob in schema, f"config_schema is missing {knob}"
        assert schema[knob]["default"] == plugin.DEFAULTS[knob], (
            f"plugin.yaml default for {knob} drifted from the code's DEFAULTS")
    # The no-spawn contract is load-bearing: it must be stated where a host
    # admin reads first, not just in the README.
    assert "never spawns" in manifest["description"]


def test_register_lands_the_plugin_methods(plugin, wired) -> None:
    names = [name for name in methods() if name.startswith("plugin.vscode.")]
    assert len(names) == 2
    for surface in ("plugin.vscode.resolve", "plugin.vscode.config"):
        assert surface in names
    from hermes_plus.rpc.registry import owns
    assert owns("plugin.vscode.resolve") is True


# ── config ───────────────────────────────────────────────────────────────────────────────


def test_schema_defaults_are_honoured(plugin, wired) -> None:
    assert plugin.config() == {
        "server_url": "",
        "workspace_root": "",
        "open_side": "right",
        "default_width": 560,
        "deep_link_enabled": True,
        "cli_pane_enabled": True,
    }
    # No server configured: the pane's ONLY door is the deep link.
    reply = plugin.resolve()
    assert reply["ok"] is True
    assert reply["embed_url"] is None
    assert reply["deep_link_only"] is True
    assert reply["deep_link_url"] == f"vscode://file/{wired.hermes_home}"


def test_settings_override_schema_defaults(plugin, wired) -> None:
    ctx = FakeCtx({"server_url": "http://127.0.0.1:8080/", "open_side": "left",
                   "default_width": 720, "cli_pane_enabled": False})
    ctx.data_dir = wired.hermes_home
    ctx.hermes_home = wired.hermes_home
    plugin.register(ctx)
    try:
        config = plugin.config()
        assert config["server_url"] == "http://127.0.0.1:8080"  # trailing / stripped
        assert config["open_side"] == "left" and config["default_width"] == 720
        assert config["cli_pane_enabled"] is False

        reply = plugin.resolve()
        assert reply["side"] == "left" and reply["width"] == 720
        assert reply["cli_enabled"] is False
        assert reply["embed_url"] == (
            f"http://127.0.0.1:8080/?folder={quote(str(wired.hermes_home), safe='')}"
        )
        assert reply["deep_link_only"] is False
    finally:
        plugin.close()


# ── resolve: per-session workspace + URLs, over RPC ──────────────────────────────────────


async def test_rpc_resolve_with_server(plugin, wired) -> None:
    ctx = FakeCtx({"server_url": "http://127.0.0.1:8080"})
    ctx.data_dir = wired.hermes_home
    ctx.hermes_home = wired.hermes_home
    plugin.register(ctx)
    try:
        reply = await _rpc(wired, "plugin.vscode.resolve", {})
        assert "error" not in reply, reply
        result = reply["result"]
        assert result["ok"] is True
        assert result["folder"] == str(wired.hermes_home)  # no session → the root
        assert result["embed_url"].startswith("http://127.0.0.1:8080/?folder=")

        # The per-session workspace: the session's own folder when it exists…
        (wired.hermes_home / "sess-1").mkdir()
        reply = await _rpc(wired, "plugin.vscode.resolve", {"session_id": "sess-1"})
        assert reply["result"]["folder"] == str(wired.hermes_home / "sess-1")

        # …the root when it does not (never an invented folder).
        reply = await _rpc(wired, "plugin.vscode.resolve", {"session_id": "ghost"})
        assert reply["result"]["folder"] == str(wired.hermes_home)
    finally:
        plugin.close()


async def test_rpc_config_echo(plugin, wired) -> None:
    reply = await _rpc(wired, "plugin.vscode.config", {})
    assert "error" not in reply, reply
    assert reply["result"]["open_side"] == "right"
    assert reply["result"]["default_width"] == 560


# ── URL encoding ─────────────────────────────────────────────────────────────────────────


def test_embed_url_encodes_the_folder_opaquely(plugin, wired) -> None:
    ctx = FakeCtx({"server_url": "http://127.0.0.1:8080"})
    ctx.data_dir = wired.hermes_home
    ctx.hermes_home = wired.hermes_home
    plugin.register(ctx)
    try:
        (wired.hermes_home / "my repo").mkdir()
        reply = plugin.resolve({"session_id": "my repo"})
        # ?folder= is one fully-quoted path value: separators included, spaces %20.
        assert reply["embed_url"] == (
            f"http://127.0.0.1:8080/?folder={quote(str(wired.hermes_home / 'my repo'), safe='')}"
        )
        assert "%20" in reply["embed_url"]
        assert " " not in reply["embed_url"].split("folder=")[-1]
        # The deep link keeps path separators literal so the OS handler sees a path.
        assert reply["deep_link_url"] == (
            f"vscode://file/{quote(str(wired.hermes_home / 'my repo'), safe='/')}"
        )
        assert reply["deep_link_url"].count("/") > 2
    finally:
        plugin.close()


def test_deep_link_can_be_switched_off(plugin, wired) -> None:
    ctx = FakeCtx({"deep_link_enabled": False})
    ctx.data_dir = wired.hermes_home
    ctx.hermes_home = wired.hermes_home
    plugin.register(ctx)
    try:
        reply = plugin.resolve()
        assert reply["deep_link_url"] is None
        assert reply["deep_link_only"] is False
    finally:
        plugin.close()


# ── the workspace fence: no traversal, no spawns ─────────────────────────────────────────


async def test_traversal_is_rejected_as_invalid_params(plugin, wired) -> None:
    outside = wired.hermes_home.parent / "outside"
    outside.mkdir(exist_ok=True)
    for bad in (
        {"folder": str(wired.hermes_home / ".." / "outside")},
        {"folder": "../outside"},
        {"folder": str(outside)},
        {"session_id": "../outside"},
        {"session_id": ".."},
    ):
        reply = await _rpc(wired, "plugin.vscode.resolve", bad)
        assert reply["error"]["code"] == INVALID_PARAMS, bad


def test_missing_explicit_folder_is_an_honest_failure(plugin, wired) -> None:
    reply = plugin.resolve({"folder": wired.hermes_home / "no-such-dir"})
    assert reply["ok"] is False
    assert reply["reason"] == "workspace-missing"
    assert reply["embed_url"] is None and reply["deep_link_url"] is None


# ── the CLI pane contract ────────────────────────────────────────────────────────────────


def test_cli_pane_flag_flows_through(plugin, wired) -> None:
    assert plugin.resolve()["cli_enabled"] is True  # the schema default
