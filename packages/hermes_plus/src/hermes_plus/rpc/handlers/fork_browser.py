"""fork.browser.{snapshot,thumbnail}: browser-step captures recorded by the bridge's post_tool_call.

Contracts mirror the fork's tui_gateway/contracts/fork_browser.py. The bridge records page
url/title/snapshot text and any screenshot path the tool result carried; the sidecar copies the
screenshot into <plus_home>/files/thumbs/ at ingest, so the RPC can serve its bytes.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import PARAMS_INVALID, RpcFault
from hermes_plus.rpc.params import read_text, require_text
from hermes_plus.rpc.registry import method

_MAX_THUMB_BYTES = 300 * 1024
_MIME_BY_SUFFIX = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".webp": "image/webp", ".gif": "image/gif"}


@method("fork.browser.thumbnail")
def thumbnail(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    tool_call_id = require_text(params, "tool_call_id", PARAMS_INVALID, "tool_call_id required")
    row = ctx.browser_thumbnails.get(tool_call_id)
    if row is None:
        return {"found": False}
    result: dict[str, Any] = {
        "found": True, "mime": None, "data_base64": None,
        "url": row["url"], "title": row["title"], "captured_at": row["captured_at"],
    }
    image = _read_image(row["image_path"])
    if image is None:
        result["reason"] = None if row["image_path"] else "no_screenshot"
        return result
    path, data = image
    if len(data) > _MAX_THUMB_BYTES:
        result["reason"] = "too_large"
        return result
    result["mime"] = _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/png")
    result["data_base64"] = base64.b64encode(data).decode("ascii")
    return result


@method("fork.browser.snapshot")
def snapshot(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    tool_call_id = require_text(params, "tool_call_id", PARAMS_INVALID, "tool_call_id required")
    row = ctx.browser_thumbnails.get(tool_call_id)
    if row is None:
        return {"found": False}
    return {
        "found": row["snapshot"] is not None,
        "text": row["snapshot"],
        "url": row["url"],
        "title": row["title"],
    }


def _read_image(image_path: str | None) -> tuple[Path, bytes] | None:
    if not image_path:
        return None
    path = Path(image_path)
    try:
        return path, path.read_bytes()
    except OSError:
        return None
