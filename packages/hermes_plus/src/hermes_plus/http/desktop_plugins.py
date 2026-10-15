"""Desktop-plugin root + entry-source routes (fork web_routers/desktop_plugins.py).

Browser-hosted renderers have no Electron main process, so the routes below
answer ``hermes:fs:desktopPluginsRoot`` / ``hermes:fs:readPluginSource``:

  * ``/api/desktop-plugins/root``            -> ``{"root": "<home>/desktop-plugins"}``
  * ``/api/desktop-plugins/list?name=<id>``  -> ``{"entries": [...]}``
  * ``/api/desktop-plugins/source?name=<id>``-> ``{"name", "path", "byteSize", "text"}``

A folder name is validated as a single path segment and re-checked by
resolution; only ``plugin.js`` and ``.hermes-package.json`` are ever readable.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hermes_plus import adapter

DESKTOP_PLUGINS_DIR = "desktop-plugins"
PACKAGE_MARKER = ".hermes-package.json"
ENTRY_FILE = "plugin.js"

# Only these two files inside a plugin folder are ever readable. Nothing else in
# the folder (README, assets, node_modules, ...) is exposed.
READABLE_FILES = frozenset({ENTRY_FILE, PACKAGE_MARKER})

# Fork: mirrors Electron's readPluginSource ceiling (16 MiB).
PLUGIN_SOURCE_MAX_BYTES = 16 * 1024 * 1024


class _HttpError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _desktop_plugins_root(profile: str | None) -> Path:
    """The app-level desktop-plugin root for this server process' home,
    created on demand, never caller-supplied and never profile-scoped beyond
    the launch home (fork desktop_plugins.py:82-99)."""
    root = adapter.paths.hermes_home(profile) / DESKTOP_PLUGINS_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def _validated_plugin_dir(root: Path, name: str) -> Path:
    candidate = (name or "").strip()
    if (
        not candidate
        or candidate in (".", "..")
        or "/" in candidate
        or "\\" in candidate
        or "\0" in candidate
        or candidate.startswith(".")
        or Path(candidate).name != candidate
    ):
        raise _HttpError(400, "Invalid desktop plugin name")

    try:
        resolved_root = root.resolve()
        target = (root / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise _HttpError(400, "Invalid path") from exc

    if target.parent != resolved_root:
        raise _HttpError(403, "Path outside the desktop plugins root")
    if not target.is_dir():
        raise _HttpError(404, "Desktop plugin not found")
    return target


def _validated_entry_file(root: Path, name: str, filename: str) -> Path:
    if filename not in READABLE_FILES:
        raise _HttpError(403, "That file is not readable over this route")

    directory = _validated_plugin_dir(root, name)
    target = directory / filename

    try:
        resolved = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise _HttpError(400, "Invalid path") from exc

    # Containment re-check after symlink resolution: a symlinked plugin.js
    # pointing anywhere else in the home must not become a file reader.
    if resolved.parent != directory.resolve():
        raise _HttpError(403, "Path outside the desktop plugin folder")

    try:
        st = resolved.stat()
    except (FileNotFoundError, NotADirectoryError):
        raise _HttpError(404, "File not found") from None
    except PermissionError:
        raise _HttpError(403, "File is not readable") from None
    except OSError as exc:
        raise _HttpError(400, str(exc) or "Invalid path") from exc

    if not stat.S_ISREG(st.st_mode):
        raise _HttpError(400, "Only regular files can be read")
    return resolved


def _list_root_entries(root: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        with os.scandir(root) as scan:
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                entries.append({
                    "name": entry.name,
                    "path": str(root / entry.name),
                    "isDirectory": entry.is_dir(follow_symlinks=False),
                })
    except (FileNotFoundError, NotADirectoryError):
        return []
    except PermissionError:
        raise _HttpError(403, "Directory is not readable") from None
    except OSError as exc:
        raise _HttpError(500, f"Could not read directory: {exc}") from exc

    entries.sort(key=lambda item: (not item["isDirectory"], item["name"].lower(), item["name"]))
    return entries


def _read_entry_source(path: Path) -> tuple[str, int]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _HttpError(500, f"Could not stat file: {exc}") from exc

    if size > PLUGIN_SOURCE_MAX_BYTES:
        raise _HttpError(413, "Plugin source too large")

    try:
        # errors="replace" matches /api/fs/read-text's decode policy; the loader
        # only needs valid JS, and a stray byte must not 500 the whole scan.
        return path.read_text(encoding="utf-8-sig", errors="replace"), size
    except PermissionError:
        raise _HttpError(403, "File is not readable") from None
    except OSError as exc:
        raise _HttpError(500, f"Could not read file: {exc}") from exc


async def desktop_plugins_root(request: Request) -> JSONResponse:
    profile = request.app.state.runtime.settings.profile
    try:
        root = _desktop_plugins_root(profile)
    except Exception:
        return JSONResponse({"root": ""})
    return JSONResponse({"root": str(root)})


async def desktop_plugins_list(request: Request) -> JSONResponse:
    profile = request.app.state.runtime.settings.profile
    name: Optional[str] = request.query_params.get("name")
    root = _desktop_plugins_root(profile)
    try:
        if name is None:
            return JSONResponse({"entries": await asyncio.to_thread(_list_root_entries, root)})
        directory = _validated_plugin_dir(root, name)
        return JSONResponse({"entries": await asyncio.to_thread(_list_root_entries, directory)})
    except _HttpError as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


async def desktop_plugins_source(request: Request) -> JSONResponse:
    profile = request.app.state.runtime.settings.profile
    name = request.query_params.get("name", "")
    file = request.query_params.get("file", ENTRY_FILE)
    root = _desktop_plugins_root(profile)
    try:
        target = await asyncio.to_thread(_validated_entry_file, root, name, file)
        text, size = await asyncio.to_thread(_read_entry_source, target)
    except _HttpError as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return JSONResponse({"name": target.name, "path": str(target), "byteSize": size, "text": text})


routes = [
    Route("/api/desktop-plugins/root", desktop_plugins_root, methods=["GET"]),
    Route("/api/desktop-plugins/list", desktop_plugins_list, methods=["GET"]),
    Route("/api/desktop-plugins/source", desktop_plugins_source, methods=["GET"]),
]
