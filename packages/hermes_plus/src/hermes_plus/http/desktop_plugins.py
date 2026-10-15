"""Desktop plugin root + entry-source routes for browser-hosted surface."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

DESKTOP_PLUGINS_DIR = "desktop-plugins"
PACKAGE_MARKER = ".hermes-package.json"
ENTRY_FILE = "plugin.js"
READABLE_FILES = frozenset({ENTRY_FILE, PACKAGE_MARKER})
PLUGIN_SOURCE_MAX_BYTES = 16 * 1024 * 1024


def _get_plus_home(runtime: Any) -> Path:
    """Get the plus_home directory."""
    try:
        from hermes_plus import adapter
        return Path(adapter.plus_home(runtime.settings.profile))
    except Exception:
        return Path.home() / ".hermes" / "hermes-plus"


def _desktop_plugins_root(runtime: Any) -> Path:
    """Get the desktop plugins root directory."""
    root = _get_plus_home(runtime) / DESKTOP_PLUGINS_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def _validated_plugin_dir(root: Path, name: str) -> Path:
    """Validate and resolve a plugin directory name."""
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
        raise ValueError("Invalid plugin name")

    try:
        resolved_root = root.resolve()
        target = (root / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid path: {exc}")

    if target.parent != resolved_root:
        raise ValueError("Path outside the desktop plugins root")

    if not target.is_dir():
        raise ValueError("Desktop plugin not found")

    return target


def _validated_entry_file(directory: Path, filename: str) -> Path:
    """Validate and resolve a file inside a plugin directory."""
    if filename not in READABLE_FILES:
        raise ValueError("That file is not readable")

    target = directory / filename

    try:
        resolved = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid path: {exc}")

    if resolved.parent != directory.resolve():
        raise ValueError("Path outside the plugin folder")

    try:
        st = resolved.stat()
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError("File not found")
    except PermissionError:
        raise ValueError("File is not readable")
    except OSError as exc:
        raise ValueError(f"Invalid path: {exc}")

    if not stat.S_ISREG(st.st_mode):
        raise ValueError("Only regular files can be read")

    return resolved


def _list_root_entries(root: Path) -> list[dict]:
    """List directory entries under root."""
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
        raise ValueError("Directory is not readable")
    except OSError as exc:
        raise ValueError(f"Could not read directory: {exc}")

    entries.sort(key=lambda item: (not item["isDirectory"], item["name"].lower(), item["name"]))
    return entries


def _read_entry_source(path: Path) -> tuple[str, int]:
    """Read file source."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Could not stat file: {exc}")

    if size > PLUGIN_SOURCE_MAX_BYTES:
        raise ValueError("Plugin source too large")

    try:
        return path.read_text(encoding="utf-8-sig", errors="replace"), size
    except PermissionError:
        raise ValueError("File is not readable")
    except OSError as exc:
        raise ValueError(f"Could not read file: {exc}")


async def desktop_plugins_root(request: Request) -> Any:
    """Get the desktop plugins root directory."""
    runtime = request.app.state.runtime
    try:
        root = _desktop_plugins_root(runtime)
        return JSONResponse({"root": str(root)})
    except Exception:
        return JSONResponse({"root": ""})


async def desktop_plugins_list(request: Request) -> Any:
    """List desktop plugins."""
    runtime = request.app.state.runtime
    name = request.query_params.get("name", "").strip()

    try:
        root = _desktop_plugins_root(runtime)
        if name:
            directory = _validated_plugin_dir(root, name)
            entries = _list_root_entries(directory)
        else:
            entries = _list_root_entries(root)
        return JSONResponse({"entries": entries})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception as exc:
        log.exception("Plugin list failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def desktop_plugins_source(request: Request) -> Any:
    """Get desktop plugin source code."""
    runtime = request.app.state.runtime
    name = request.query_params.get("name", "").strip()
    file = request.query_params.get("file", ENTRY_FILE).strip()

    if not name:
        return JSONResponse({"detail": "name required"}, status_code=400)

    try:
        root = _desktop_plugins_root(runtime)
        directory = _validated_plugin_dir(root, name)
        target = _validated_entry_file(directory, file)
        text, size = _read_entry_source(target)
        return JSONResponse({
            "name": target.name,
            "path": str(target),
            "byteSize": size,
            "text": text,
        })
    except ValueError as exc:
        status = 400 if "Invalid" in str(exc) or "required" in str(exc) else 404
        return JSONResponse({"detail": str(exc)}, status_code=status)
    except Exception as exc:
        log.exception("Plugin source failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


routes = [
    Route("/api/desktop-plugins/root", desktop_plugins_root, methods=["GET"]),
    Route("/api/desktop-plugins/list", desktop_plugins_list, methods=["GET"]),
    Route("/api/desktop-plugins/source", desktop_plugins_source, methods=["GET"]),
]
