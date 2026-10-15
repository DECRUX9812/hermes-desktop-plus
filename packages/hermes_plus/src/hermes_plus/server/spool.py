"""The spool ingester: tails the bridge's spool files into plus.db.

Per-file byte offsets live in plus.db (spool_offsets) so a restart never double-ingests; only
complete lines are consumed. Fully ingested files older than a day are deleted. Every record
handler is defensive: a malformed line is skipped and counted, never fatal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from hermes_plus.store import (
    ApprovalJournal,
    BrowserThumbnails,
    DelegationReports,
    SpoolOffsets,
    Store,
    ToolCalls,
)

log = logging.getLogger(__name__)

_MAX_THUMB_BYTES = 300 * 1024
_FILE_MAX_AGE_SECONDS = 24 * 60 * 60


class SpoolIngester:
    def __init__(self, store: Store, plus_home: Path) -> None:
        self.store = store
        self.plus_home = Path(plus_home)
        self.tool_calls = ToolCalls(store)
        self.delegations = DelegationReports(store)
        self.thumbnails = BrowserThumbnails(store)
        self.approvals = ApprovalJournal(store)
        self._offsets = SpoolOffsets(store)

    def ingest_once(self) -> dict[str, int]:
        """Consume every complete new line; returns per-kind counters for tests and logging."""
        stats = {"tool_call": 0, "delegation": 0, "browser_thumbnail": 0,
                 "approval_request": 0, "approval_decision": 0, "skipped": 0}
        directory = self.plus_home / "spool"
        try:
            files = sorted(directory.glob("*.jsonl"))
        except OSError:
            return stats
        for path in files:
            try:
                self._ingest_file(path, stats)
            except OSError:
                log.exception("spool ingest failed for %s", path)
        self._prune(directory)
        return stats

    # -- file handling ------------------------------------------------------------------

    def _ingest_file(self, path: Path, stats: dict[str, int]) -> None:
        start = self._offsets.get(path.name)
        try:
            size = path.stat().st_size
        except OSError:
            return
        if start >= size:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read()
        consumed = 0
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break  # writer has not finished this line yet
            consumed += len(line)
            self._apply_line(line, stats)
        if consumed:
            self._offsets.set(path.name, start + consumed)

    def _apply_line(self, line: bytes, stats: dict[str, int]) -> None:
        try:
            record = json.loads(line.decode("utf-8"))
            if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                raise ValueError("record is not an object with a payload")
            kind = str(record.get("kind") or "")
            ts = float(record.get("ts") or time.time())
            session_id = str(record.get("session_id") or "")
            handler = _HANDLERS.get(kind)
            if handler is None:
                stats["skipped"] += 1
                return
            handler(self, record["payload"], session_id, ts, stats)
        except Exception:
            stats["skipped"] += 1
            log.exception("skipping a malformed spool record")

    # -- record handlers ------------------------------------------------------------------

    def _apply_tool_call(self, payload: dict[str, Any], session_id: str, ts: float,
                         stats: dict[str, int]) -> None:
        self.tool_calls.record(
            tool_call_id=str(payload.get("tool_call_id") or f"tool-{ts:.6f}"),
            session_id=session_id,
            tool_name=str(payload.get("tool_name") or ""),
            status=str(payload.get("status") or ""),
            error_type=payload.get("error_type"),
            error_message=payload.get("error_message"),
            args=payload.get("args"),
            result=payload.get("result"),
            duration_ms=_as_int(payload.get("duration_ms")),
            created_at=ts)
        stats["tool_call"] += 1

    def _apply_delegation(self, payload: dict[str, Any], session_id: str, ts: float,
                          stats: dict[str, int]) -> None:
        entries = payload.get("results")
        if not isinstance(entries, list):
            entries = []
        child_ids = payload.get("child_session_ids")
        self.delegations.record_from_spool(
            delegation_id=str(payload.get("delegation_id") or f"delegation-{ts:.6f}"),
            session_id=session_id,
            state=str(payload.get("state") or "completed"),
            entries=[entry for entry in entries if isinstance(entry, dict)],
            child_session_ids=child_ids if isinstance(child_ids, dict) else {},
            group=payload.get("group"),
            duration_seconds=_as_float(payload.get("total_duration_seconds")
                                       or payload.get("duration_seconds")),
            completed_at=ts)
        stats["delegation"] += 1

    def _apply_browser_thumbnail(self, payload: dict[str, Any], session_id: str, ts: float,
                                 stats: dict[str, int]) -> None:
        tool_call_id = str(payload.get("tool_call_id") or f"browser-{ts:.6f}")
        self.thumbnails.record(
            tool_call_id=tool_call_id,
            session_id=session_id,
            tool_name=str(payload.get("tool_name") or ""),
            url=payload.get("url"),
            title=payload.get("title"),
            captured_at=_as_float(payload.get("captured_at")) or ts,
            image_path=self._copy_screenshot(tool_call_id, payload.get("screenshot_path")),
            snapshot=payload.get("snapshot"))
        stats["browser_thumbnail"] += 1

    def _apply_approval_request(self, payload: dict[str, Any], session_id: str, ts: float,
                                stats: dict[str, int]) -> None:
        self.approvals.record_request(
            request_id=_approval_id(payload, session_id),
            session_id=session_id,
            tool_call_id="",
            tool_name=str(payload.get("tool_name") or ""),
            command=str(payload.get("command") or ""),
            description=str(payload.get("description") or ""))
        stats["approval_request"] += 1

    def _apply_approval_decision(self, payload: dict[str, Any], session_id: str, ts: float,
                                 stats: dict[str, int]) -> None:
        choice = str(payload.get("choice") or "")
        if choice:
            self.approvals.decide(_approval_id(payload, session_id), choice)
        stats["approval_decision"] += 1

    # -- helpers ------------------------------------------------------------------

    def _copy_screenshot(self, tool_call_id: str, screenshot_path: Any) -> str | None:
        """Keep the browser screenshot under our files/ tree so the RPC can serve it later."""
        if not isinstance(screenshot_path, str) or not screenshot_path:
            return None
        source = Path(screenshot_path)
        try:
            if not source.is_file() or source.stat().st_size > _MAX_THUMB_BYTES:
                return None
            target = self.plus_home / "files" / "thumbs" / f"{tool_call_id}{source.suffix.lower()}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
            return str(target)
        except OSError:
            log.exception("could not keep the browser screenshot for %s", tool_call_id)
            return None

    def _prune(self, directory: Path) -> None:
        now = time.time()
        try:
            entries = list(directory.glob("*.jsonl"))
        except OSError:
            return
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime <= _FILE_MAX_AGE_SECONDS:
                continue
            if self._offsets.get(path.name) >= stat.st_size:
                try:
                    path.unlink()
                except OSError:
                    log.exception("could not delete ingested spool file %s", path)
                else:
                    self._offsets.forget(path.name)


def _approval_id(payload: dict[str, Any], session_id: str) -> str:
    digest = hashlib.sha256("|".join([
        session_id, str(payload.get("session_key") or ""), str(payload.get("command") or ""),
        str(payload.get("description") or ""),
    ]).encode("utf-8")).hexdigest()
    return f"bridge-{digest[:16]}"


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


_HANDLERS = {
    "tool_call": SpoolIngester._apply_tool_call,
    "delegation": SpoolIngester._apply_delegation,
    "browser_thumbnail": SpoolIngester._apply_browser_thumbnail,
    "approval_request": SpoolIngester._apply_approval_request,
    "approval_decision": SpoolIngester._apply_approval_decision,
}
