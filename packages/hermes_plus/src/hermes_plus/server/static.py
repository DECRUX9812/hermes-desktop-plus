"""Static UI hosting: packaged build, SPA fallback, and runtime-global injection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response

from hermes_plus import __version__
from hermes_plus.server.proxy import proxy_http

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache"
# Vite-style content hashes: ``assets/index-B3k2x9Qz.js``.
_HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]{1,8}$")
# Dev fallback: a local packages/ui build next to the Python package. Module
# level so a test can point it away from a checkout that has been built.
_DEV_UI_DIR = Path(__file__).resolve().parents[3].parent / "ui" / "dist-webapp"


def _ui_root(settings: Any) -> Path | None:
    root = Path(settings.ui_dir)
    if (root / "index.html").is_file():
        return root
    if (_DEV_UI_DIR / "index.html").is_file():
        return _DEV_UI_DIR
    return root if root.is_dir() else None


def _script_escape(value: Any) -> str:
    """JSON-encode a value so it cannot close the host ``<script>`` element."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _inject(html: str, settings: Any, runtime: Any) -> str:
    base_path = settings.base_path if settings.base_path not in ("", "/") else ""
    compat = runtime.compat
    # The renderer reads this object as-is, so it is serialised, never
    # hand-written: a stray brace here kills the whole bootstrap script and the
    # app boots into "Desktop IPC bridge is unavailable".
    plus_config = {
        "apiBase": "",
        "wsBase": "",
        "basePath": settings.base_path,
        "version": __version__,
        "compat": {
            "status": getattr(compat, "status", "unknown"),
            "message": getattr(compat, "message", ""),
        },
    }
    bootstrap = (
        "<script>"
        f"window.__HERMES_SESSION_TOKEN__={_script_escape(runtime.auth.token)};"
        f"window.__HERMES_BASE_PATH__={_script_escape(base_path)};"
        f"window.__HERMES_AUTH_REQUIRED__={'true' if settings.auth != 'none' else 'false'};"
        f"window.__HERMES_UI_SURFACE__={_script_escape('dashboard')};"
        f"window.__HERMES_PLUS__={_script_escape(plus_config)};"
        "</script>"
    )
    lowered = html.lower()
    head_close = lowered.find("</head>")
    if head_close != -1:
        return html[:head_close] + bootstrap + html[head_close:]
    return bootstrap + html


def _safe_path(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


async def serve(request: Request, settings: Any, runtime: Any) -> Response:
    if settings.dev_ui_url:
        return await proxy_http(request, runtime, settings.dev_ui_url, auth_upstream=False)

    root = _ui_root(settings)
    if root is None:
        return Response("Frontend not built", status_code=404)

    relative = request.path_params.get("path", "").lstrip("/")
    candidate = _safe_path(root, relative) if relative else None
    if candidate is not None and candidate.is_file():
        headers = {"Cache-Control": _IMMUTABLE if _HASHED_ASSET.search(candidate.name) else _NO_CACHE}
        return FileResponse(candidate, headers=headers)

    # SPA fallback: non-asset paths render index.html; missing assets 404.
    if "." in relative.rsplit("/", 1)[-1]:
        return Response("Not found", status_code=404)
    index = root / "index.html"
    try:
        html = index.read_text(encoding="utf-8-sig")
    except OSError:
        return Response("Frontend not built", status_code=404)
    return HTMLResponse(_inject(html, settings, runtime), headers={"Cache-Control": _NO_CACHE})
