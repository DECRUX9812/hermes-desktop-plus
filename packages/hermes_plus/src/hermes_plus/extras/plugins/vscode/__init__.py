"""VS Code: the fork's Code pane as an add-on plugin — the user's editor beside
the chat, opened on the session's workspace folder.

Ported from the fork's apps/desktop/src/store/code-pane.ts ($codeOpen pane
state, loadCodeServer's per-folder ask) and its openVsCode preload capability
(the Electron main process spawns and serves the user's own VS Code on
loopback). The browser add-on has no Electron preload, and this plugin never
spawns a process, so the browser-capable equivalent is:

- a configured code-server / openvscode-server URL (``server_url``) embedded
  beside the chat in a sandboxed iframe, pointed at the workspace folder via
  the server's own ``?folder=`` query parameter;
- a ``vscode://file/...`` deep link (the fork's editor-handoff URL form) that
  hands the folder to the desktop's installed VS Code — the ONLY door when no
  server URL is configured (``deep_link_only``), and a secondary "open in VS
  Code" affordance when one is.

Workspace resolution is fenced: one folder per session —
``<workspace_root>/<session_id>`` when that directory exists, otherwise
workspace_root itself (an empty root falls back to the profile home). Every
folder — explicit or derived from a session id — is resolved (``..`` and
symlinks included) and must land under workspace_root; anything that escapes
is an INVALID_PARAMS fault. The plugin spawns no processes and performs no
reads outside the fence: it only formats URLs over paths the user configured.

All knobs come from the plugin's config_schema, read ONLY through
``ctx.get_config`` (the activity pattern): the ctx is captured at register
time, and ``ctx.get_config`` re-reads config.yaml on every call so edits apply
without a restart. The pane owns no state, so there is no plugin store —
``resolve`` is a pure read-and-format.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hermes_plus.rpc.errors import INVALID_PARAMS, RpcFault

logger = logging.getLogger("hermes_plus.plugins.vscode")

# Must mirror the defaults declared in plugin.yaml's config_schema (the manifest
# test pins the two together). ctx.get_config already resolves schema defaults
# when the host honours them; these keep the plugin correct on hosts that pass
# through unknown keys.
DEFAULTS: dict[str, Any] = {
    "server_url": "",
    "workspace_root": "",
    "open_side": "right",
    "default_width": 560,
    "deep_link_enabled": True,
    "cli_pane_enabled": True,
    "debug": False,
}

_PLUGIN_CTX: Any = None


def _setting(name: str, default: Any = None) -> Any:
    """``plugins.entries.vscode.settings.<name>`` through the ctx captured at
    register time, or ``default`` when register has not run (the API is inert anyway)."""
    if _PLUGIN_CTX is None:
        return default
    try:
        return _PLUGIN_CTX.get_config(name, default)
    except Exception:  # pragma: no cover - a broken host must not kill a resolve
        return default


def _knob(name: str) -> Any:
    value = _setting(name, DEFAULTS[name])
    return DEFAULTS[name] if value is None else value


def _hermes_home() -> Path:
    """The profile home the ctx (or env) names — the workspace root's fallback."""
    home = (
        getattr(_PLUGIN_CTX, "hermes_home", None)
        or os.environ.get("HERMES_HOME")
        or str(Path.home() / ".hermes")
    )
    return Path(home)


# ── the workspace fence ──────────────────────────────────────────────────────────────────


def _workspace_root() -> Path:
    """The configured root, resolved — the only tree the pane may open. Empty
    falls back to the profile home."""
    raw = str(_knob("workspace_root") or "").strip()
    root = Path(raw).expanduser() if raw else _hermes_home()
    return root.resolve()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_folder(params: dict[str, Any]) -> tuple[Path, Path]:
    """(root, folder) for a resolve request. An explicit ``folder`` or a
    session-derived one must land under the root once resolved — a traversal
    (`..`, a symlink, a session id shaped like a path) is a fault, not a read."""
    root = _workspace_root()
    requested = str(params.get("folder") or "").strip()
    session_id = str(params.get("session_id") or "").strip()

    if requested:
        folder = Path(requested).expanduser()
        if not folder.is_absolute():
            folder = root / folder
        folder = folder.resolve()
        if not _is_under(folder, root):
            raise RpcFault(INVALID_PARAMS, "folder escapes workspace_root")
    elif session_id:
        candidate = (root / session_id).resolve()
        if not _is_under(candidate, root):
            raise RpcFault(INVALID_PARAMS, "session workspace escapes workspace_root")
        # Per-session workspace: the session's own folder when it exists, the
        # root itself otherwise (the fork opens the chat's project folder; a
        # session without one gets the configured root).
        folder = candidate if candidate.is_dir() else root
    else:
        folder = root
    return root, folder


# ── the pane API ─────────────────────────────────────────────────────────────────────────


def resolve(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """One resolve for the pane: the workspace folder plus the URLs that open
    it. ``embed_url`` is the code-server iframe src (None without a configured
    server); ``deep_link_url`` is the vscode:// hand-off; ``deep_link_only``
    says the deep link is the pane's ONLY door (no server configured). Pure
    formatting — no process is spawned, nothing is read outside the fence."""
    params = params or {}
    _root, folder = _resolve_folder(params)

    if not folder.is_dir():
        # An explicitly requested folder that is not there: honest failure, no
        # invented workspace (a session id already fell back to the root above).
        return {
            "ok": False,
            "reason": "workspace-missing",
            "folder": str(folder),
            "server_url": str(_knob("server_url") or "").strip().rstrip("/"),
            "embed_url": None,
            "deep_link_url": None,
            "deep_link_only": False,
            "side": str(_knob("open_side")),
            "width": int(_knob("default_width")),
            "cli_enabled": bool(_knob("cli_pane_enabled")),
        }

    server = str(_knob("server_url") or "").strip().rstrip("/")
    # The server's own folder parameter: one opaque, fully-quoted path value
    # (safe='' encodes the separators too), so spaces and unicode survive.
    embed = f"{server}/?folder={quote(str(folder), safe='')}" if server else None
    # The fork's editor-handoff deep-link form: vscode://file/<path>, slashes
    # kept literal so the OS handler sees a path, everything else quoted.
    deep_link = (
        f"vscode://file/{quote(str(folder), safe='/')}" if _knob("deep_link_enabled") else None
    )
    return {
        "ok": True,
        "folder": str(folder),
        "server_url": server,
        "embed_url": embed,
        "deep_link_url": deep_link,
        "deep_link_only": embed is None and deep_link is not None,
        "side": str(_knob("open_side")),
        "width": int(_knob("default_width")),
        "cli_enabled": bool(_knob("cli_pane_enabled")),
    }


def config() -> dict[str, Any]:
    """The effective UI-relevant knobs (the UI sizes and gates the pane with them)."""
    return {
        "server_url": str(_knob("server_url") or "").strip().rstrip("/"),
        "workspace_root": str(_knob("workspace_root") or "").strip(),
        "open_side": str(_knob("open_side")),
        "default_width": int(_knob("default_width")),
        "deep_link_enabled": bool(_knob("deep_link_enabled")),
        "cli_pane_enabled": bool(_knob("cli_pane_enabled")),
    }


def register(ctx) -> None:
    global _PLUGIN_CTX
    _PLUGIN_CTX = ctx
    # The RPC surface registers through the sidecar registry's sanctioned plugin
    # hook, here where every other feature wires up. Lazy import + guard: a host
    # without the sidecar package still loads the vscode API, just with no RPC door.
    try:
        from .rpc import register_rpc
    except Exception:
        logger.debug("vscode: rpc module unavailable; RPC surface not registered")
    else:
        try:
            register_rpc()
        except Exception:
            logger.exception("vscode: RPC registration failed; the pane API continues without it")
    if _knob("debug"):
        logger.debug("vscode registered; workspace root resolves under %s", _workspace_root())


def close() -> None:
    """Drop the plugin's RPC methods (tests and host shutdown). No store to close:
    the pane owns no state."""
    try:
        from .rpc import unregister_rpc
    except Exception:
        pass
    else:
        unregister_rpc()


__all__ = [
    "DEFAULTS", "_is_under", "_resolve_folder", "_workspace_root",
    "close", "config", "register", "resolve",
]
