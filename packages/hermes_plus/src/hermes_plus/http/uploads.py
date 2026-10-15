"""Authenticated browser-to-host attachment staging."""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_UPLOAD_RETENTION_SECONDS = 7 * 24 * 60 * 60
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(value: str | None) -> str:
    name = Path(str(value or "attachment"))
    stem = _SAFE_FILENAME.sub("-", name.stem).strip(".-") or "attachment"
    suffix = _SAFE_FILENAME.sub("-", name.suffix).rstrip(".-")[:119]
    return stem[:120 - len(suffix)] + suffix


async def upload_chat_file(request: Request) -> Any:
    """Stage a file upload and return its path."""
    runtime = request.app.state.runtime

    try:
        form = await request.form()
        file = form.get("file")
        if not file:
            return JSONResponse({"detail": "No file provided"}, status_code=400)

        content = await file.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            cap_mib = _MAX_UPLOAD_BYTES // (1024 * 1024)
            return JSONResponse(
                {"detail": f"File is too large; cap is {cap_mib} MiB"}, status_code=413
            )

        # Get the plus_home path from adapter
        try:
            from hermes_plus import adapter
            plus_home = Path(adapter.plus_home(runtime.settings.profile))
        except Exception:
            plus_home = Path.home() / ".hermes" / "hermes-plus"

        upload_root = plus_home / "files" / "uploads"
        upload_root.mkdir(parents=True, exist_ok=True)

        from datetime import datetime
        now = datetime.utcnow()
        month_dir = upload_root / now.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)

        filename = _safe_filename(file.filename or "attachment")
        target = month_dir / f"{secrets.token_hex(8)}-{filename}"

        with open(target, "wb") as f:
            f.write(content)

        return JSONResponse({
            "ok": True,
            "path": str(target),
            "size": len(content),
        })
    except Exception as exc:
        log.exception("Upload failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


routes = [
    Route("/api/chat/file-upload", upload_chat_file, methods=["POST"]),
]
