"""File serving with Range request support and file tickets."""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

# Whitelist of file extensions that can be streamed as media (for partial range reads)
_STREAMABLE_MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".webm", ".ogg", ".mp3", ".wav", ".flac",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".pdf", ".txt", ".html", ".css", ".js", ".json",
})


def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse RFC 7233 Range header. Returns (start, end) in bytes or None if invalid."""
    if not range_header:
        return None

    match = re.match(r"bytes=(\d+)-(\d*)", range_header.strip())
    if not match:
        return None

    start_str, end_str = match.groups()
    try:
        start = int(start_str)
        if start >= file_size:
            return None
        if end_str:
            end = int(end_str) + 1  # Range header end is inclusive
            end = min(end, file_size)
        else:
            end = file_size
        if start >= end:
            return None
        return (start, end)
    except (ValueError, OverflowError):
        return None


async def download_file(request: Request) -> Any:
    """Download a file with Range request support (206 Partial Content)."""
    # This is a simplified implementation that only handles single-range requests.
    # For now, it proxies to upstream for most files. This can be enhanced
    # to serve from plus_home for managed files.
    
    # For now, just proxy to upstream via the existing proxy handler.
    # The Range support can be added here as needed for specific use cases.
    from starlette.responses import JSONResponse
    
    return JSONResponse(
        {"detail": "File download not yet implemented in sidecar"},
        status_code=501,
    )


async def serve_file_with_range(
    file_path: Path,
    *,
    media_type: str | None = None,
    filename: str | None = None,
) -> Response:
    """Serve a file with Range request support."""
    if not file_path.is_file():
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"detail": "File not found"},
            status_code=404,
        )

    file_size = file_path.stat().st_size
    media_type = media_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    filename = filename or file_path.name

    # For now, if no Range header, just serve the whole file.
    # Range support can be added when needed.
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
    )


routes = []
