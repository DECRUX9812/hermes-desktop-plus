"""Authenticated browser-to-host attachment staging (fork web_routers/uploads.py).

Response shape (fork uploads.py:186-195): ``{"ok", "path", "size"}`` plus
``staged_upload`` when an install identity exists. Files land under
``$HERMES_HOME/uploads/web-<hex>-<safe-name>`` with 0600, exactly where the
fork's gateway-side ``file.attach`` staging expects to find them.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import stat
import time
import uuid
from contextlib import suppress
from pathlib import Path

from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hermes_plus import adapter

log = logging.getLogger(__name__)

# Fork hermes_constants.py:29: WEBAPP_ATTACHMENT_MAX_BYTES.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
# Fork web_upload_limit.py:16: the body cap adds room for multipart framing.
CHAT_FILE_BODY_MAX_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
_UPLOAD_RETENTION_SECONDS = 7 * 24 * 60 * 60
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_CHUNK_BYTES = 1024 * 1024
_REJECTION = JSONResponse(status_code=413, content={"detail": "Upload request is too large"})


def safe_filename(value: str | None) -> str:
    name = Path(str(value or "attachment"))
    stem = _SAFE_FILENAME.sub("-", name.stem).strip(".-") or "attachment"
    suffix = _SAFE_FILENAME.sub("-", name.suffix).rstrip(".-")[:119]
    return stem[:120 - len(suffix)] + suffix


def _prune_stale_uploads(root: Path, *, now: float | None = None) -> None:
    cutoff = (time.time() if now is None else now) - _UPLOAD_RETENTION_SECONDS
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith("web-"):
            continue
        try:
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(metadata.st_mode) and metadata.st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def install_id(plus_home: Path) -> str:
    """A stable per-install random id, minted on first use (fork get_install_id)."""
    path = plus_home / "install_id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    fresh = uuid.uuid4().hex
    with suppress(OSError):
        plus_home.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh, encoding="utf-8")
        os.chmod(path, 0o600)
    return fresh


async def upload_chat_file(request: Request) -> JSONResponse:
    runtime = request.app.state.runtime
    try:
        form = await request.form()
    except MultiPartException:
        return _REJECTION
    file = form.get("file")
    if file is None or isinstance(file, str):
        return JSONResponse({"detail": "No file provided"}, status_code=400)

    total = file.size or 0
    if total > MAX_UPLOAD_BYTES:
        cap_mib = MAX_UPLOAD_BYTES // (1024 * 1024)
        return JSONResponse(
            {"detail": f"File is too large; cap is {cap_mib} MiB"}, status_code=413
        )

    home = adapter.paths.hermes_home(runtime.settings.profile)
    upload_root = home / "uploads"
    try:
        upload_root.mkdir(parents=True, exist_ok=True)
        upload_root.chmod(0o700)
    except OSError as exc:
        return JSONResponse({"detail": f"Could not create upload directory: {exc}"}, status_code=500)

    _prune_stale_uploads(upload_root)
    target = upload_root / f"web-{secrets.token_hex(8)}-{safe_filename(file.filename)}"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            while chunk := await file.read(_CHUNK_BYTES):
                handle.write(chunk)
    except OSError as exc:
        with suppress(OSError):
            target.unlink(missing_ok=True)
        return JSONResponse({"detail": f"Could not stage file: {exc}"}, status_code=500)

    result: dict[str, object] = {"ok": True, "path": str(target), "size": total}
    plus_home = adapter.paths.plus_home(runtime.settings.profile)
    result["staged_upload"] = {
        "install_id": install_id(plus_home),
        "path": str(target),
        "profile_home": str(home),
    }
    return JSONResponse(result)


class ChatFileUploadLimitMiddleware:
    """Bound /api/chat/file-upload bodies before the multipart parser spools them.

    Port of fork web_upload_limit.py: responses carry the fork's exact 413
    shape, and the rejection replaces whatever status the parser would 400 with.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/api/chat/file-upload":
            await self.app(scope, receive, send)
            return

        length = Headers(scope=scope).get("content-length", "").lstrip("0") or "0"
        if (
            length.isascii()
            and length.isdecimal()
            and (len(length) > len(str(CHAT_FILE_BODY_MAX_BYTES)) or int(length) > CHAT_FILE_BODY_MAX_BYTES)
        ):
            await _REJECTION(scope, receive, send)
            return

        received = 0
        exceeded = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > CHAT_FILE_BODY_MAX_BYTES:
                    exceeded = True
                    # Starlette's multipart parser closes every open spool on
                    # MultiPartException; raising here keeps the rejected chunk
                    # away from the parser and converts its 400 into our 413.
                    raise MultiPartException("Upload request is too large")
            return message

        async def limited_send(message: Message) -> None:
            if not exceeded:
                await send(message)
            elif message["type"] == "http.response.start":
                await _REJECTION(scope, receive, send)

        await self.app(scope, limited_receive, limited_send)


routes = [
    Route("/api/chat/file-upload", upload_chat_file, methods=["POST"]),
]
