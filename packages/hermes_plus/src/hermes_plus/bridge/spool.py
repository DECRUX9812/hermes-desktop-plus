"""Append-only event spool: the bridge's only output channel.

Hooks append one JSON object per line to ``<HERMES_HOME>/hermes-plus/spool/<pid>-<date>.jsonl``.
The sidecar tails these files into plus.db. Writes are small single ``os.write`` appends and are
wrapped so a full disk or a permission error can never break a tool call.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

SPOOL_VERSION = 1
_MAX_LINE_BYTES = 256 * 1024


def truncate(value: Any, limit: int) -> Any:
    """Bound a payload string; longer values are cut so the spool cannot grow without limit."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


class Spool:
    """Process-local writer; one file per pid per day, opened lazily and re-opened per append."""

    def __init__(self, plus_home: Path) -> None:
        self.directory = Path(plus_home) / "spool"
        self._lock = threading.Lock()
        self._failures = 0
        self._warned = False

    def append(self, kind: str, session_id: str, payload: dict[str, Any],
               profile: str | None = None) -> bool:
        """Append one record; returns False (never raises) when the record could not be written."""
        record = {
            "v": SPOOL_VERSION,
            "ts": time.time(),
            "kind": kind,
            "profile": profile or "",
            "session_id": session_id or "",
            "payload": payload,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return False
        if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            line = json.dumps({**record, "payload": {"truncated": True}}, ensure_ascii=False)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{os.getpid()}-{time.strftime('%Y%m%d')}.jsonl"
            data = (line + "\n").encode("utf-8")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            return True
        except OSError:
            self._failures += 1
            if not self._warned:
                self._warned = True
                try:
                    import logging
                    logging.getLogger(__name__).warning(
                        "hermes-plus spool write failed (%s); spool records are dropped until "
                        "the next success", self._failures)
                except Exception:
                    pass
            return False


_INSTANCE: Spool | None = None
_INSTANCE_LOCK = threading.Lock()


def get_spool(plus_home: Path) -> Spool:
    """One writer per Hermes process; the sidecar never uses this module."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Spool(plus_home)
        return _INSTANCE
