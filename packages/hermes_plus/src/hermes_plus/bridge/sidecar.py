"""Loopback RPC client for the Hermes Plus sidecar.

Stateful tools (bot teams, mailbox) read ``<plus_home>/sidecar.json`` (written by the sidecar,
mode 0600) and POST ``{"method", "params"}`` to its ``/api/plus/bridge/rpc`` route. Everything is
stdlib (``urllib.request``) and every failure becomes an error result — never an exception out of
a tool call.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SIDECAR_FILE = "sidecar.json"
BRIDGE_HEADER = "X-Hermes-Plus-Bridge"
DEFAULT_TIMEOUT = 20.0


def read_sidecar(plus_home: Path) -> dict[str, Any] | None:
    """The sidecar's contact card, or None when it is not running."""
    path = Path(plus_home) / SIDECAR_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("url") and data.get("token") else None


def call(plus_home: Path, method: str, params: dict[str, Any] | None = None,
         timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Call one plus RPC method on the sidecar. Returns the result object or {"error": ...}."""
    sidecar = read_sidecar(plus_home)
    if sidecar is None:
        return {"error": "Hermes Plus sidecar is not running — start it with 'hermes-plus serve'."}
    body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
    request = urllib.request.Request(
        f"{sidecar['url'].rstrip('/')}/api/plus/bridge/rpc",
        data=body,
        headers={"Content-Type": "application/json", BRIDGE_HEADER: str(sidecar["token"])},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as err:
        log.debug("sidecar rpc %s failed: %s", method, err)
        return {"error": f"Hermes Plus sidecar call failed: {err}"}
    if not isinstance(payload, dict):
        return {"error": "Hermes Plus sidecar returned a malformed response"}
    if "error" in payload and payload.get("result") is None:
        error = payload["error"]
        if isinstance(error, dict):
            return {"error": str(error.get("message", error))}
        return {"error": str(error)}
    result = payload.get("result")
    return result if isinstance(result, dict) else {}
