"""Hermes built-in memory files (<home>/memories/MEMORY.md and USER.md): format, lock, and atomic writes.

Mirrors upstream tools/memory_tool_store.py. The format and the `<file>.lock` flock protocol
must stay compatible with the agent's own memory tool, which edits the same files.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hermes_plus.adapter.config import load_config
from hermes_plus.adapter.paths import hermes_home
from hermes_plus.adapter.threat_patterns import first_threat

Target = Literal["memory", "user"]
TARGETS: tuple[Target, ...] = ("memory", "user")
DEFAULT_LIMITS: dict[str, int] = {"memory": 2200, "user": 1375}
ENTRY_DELIMITER = "\n§\n"
_FILE_NAMES: dict[str, str] = {"memory": "MEMORY.md", "user": "USER.md"}


class MemoryRefused(Exception):
    """The write was refused: invalid or empty text, a threat, a duplicate, the char budget, or drift."""


class MemoryItemChanged(Exception):
    """The entry id no longer matches any entry on disk."""


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    target: str
    text: str


def entry_id(target: str, text: str) -> str:
    return hashlib.sha1(f"{target}\0{text}".encode()).hexdigest()[:12]


def _path(profile: str, target: str) -> Path:
    return hermes_home(profile) / "memories" / _FILE_NAMES[target]


def _limits(profile: str) -> dict[str, int]:
    section = load_config(profile).get("memory")
    section = section if isinstance(section, dict) else {}
    return {
        "memory": int(section.get("memory_char_limit", DEFAULT_LIMITS["memory"])),
        "user": int(section.get("user_char_limit", DEFAULT_LIMITS["user"])),
    }


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        raise MemoryRefused(
            f"Refusing to write {path.name}: the file exists but could not be read "
            f"({err.__class__.__name__}). Nothing was changed."
        ) from err


def _parse(raw: str) -> list[str]:
    entries = [part.strip() for part in raw.split(ENTRY_DELIMITER)]
    return list(dict.fromkeys(entry for entry in entries if entry))


def _used(entries: list[str]) -> int:
    return len(ENTRY_DELIMITER.join(entries))


def _check_budget(entries: list[str], limit: int) -> None:
    total = _used(entries)
    if total > limit:
        raise MemoryRefused(
            f"Memory at {total:,}/{limit:,} chars would exceed the limit by {total - limit:,} chars."
        )


def _scan(text: str) -> None:
    if message := first_threat(text):
        raise MemoryRefused(message)


def _refuse_drift(path: Path, raw: str, limit: int) -> None:
    """Refuse when an entry is bigger than the whole budget: that is free-form text written
    without § delimiters, and rewriting the file would discard it. Snapshot it first."""
    if max(map(len, _parse(raw)), default=0) <= limit:
        return
    backup = path.with_name(f"{path.name}.bak.{int(time.time())}")
    backup.write_text(raw, encoding="utf-8")
    raise MemoryRefused(
        f"Refusing to write {path.name}: it holds an entry larger than the store's char limit, "
        f"which means free-form text was written without § delimiters. A snapshot was saved to "
        f"{backup}. Rewrite the file as a clean §-delimited list, then retry."
    )


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+") as handle:
        os.fchmod(fd, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".mem_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(ENTRY_DELIMITER.join(entries))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _read_entries(profile: str, target: str) -> list[str]:
    return _parse(_read(_path(profile, target)))


def snapshot(profile: str) -> dict[str, Any]:
    limits = _limits(profile)
    entries: list[MemoryEntry] = []
    usage: dict[str, dict[str, int]] = {}
    for target in TARGETS:
        texts = _read_entries(profile, target)
        entries.extend(MemoryEntry(entry_id(target, text), target, text) for text in texts)
        usage[target] = {"used": _used(texts), "limit": limits[target]}
    return {"entries": entries, "usage": usage}


def add(profile: str, target: str, content: str) -> None:
    if target not in TARGETS:
        raise MemoryRefused(f"Invalid memory target '{target}'. Use 'memory' or 'user'.")
    text = content.strip()
    if not text:
        raise MemoryRefused("Content cannot be empty.")
    _scan(text)
    path = _path(profile, target)
    limit = _limits(profile)[target]
    with _locked(path):
        entries = _parse(_read(path))
        if text in entries:
            return
        candidate = [*entries, text]
        _check_budget(candidate, limit)
        _write(path, candidate)


def _locate(profile: str, item_id: str) -> tuple[str, str]:
    for target in TARGETS:
        for text in _read_entries(profile, target):
            if entry_id(target, text) == item_id:
                return target, text
    raise MemoryItemChanged(item_id)


def replace(profile: str, item_id: str, content: str) -> None:
    text = content.strip()
    if not text:
        raise MemoryRefused("new_content cannot be empty. Use 'remove' to delete entries.")
    _scan(text)
    target, current = _locate(profile, item_id)
    path = _path(profile, target)
    limit = _limits(profile)[target]
    with _locked(path):
        raw = _read(path)
        _refuse_drift(path, raw, limit)
        entries = _parse(raw)
        if current not in entries:
            raise MemoryItemChanged(item_id)
        if text in entries and text != current:
            raise MemoryRefused("Entry already exists.")
        candidate = [text if entry == current else entry for entry in entries]
        _check_budget(candidate, limit)
        _write(path, candidate)


def remove(profile: str, item_id: str) -> None:
    target, current = _locate(profile, item_id)
    path = _path(profile, target)
    limit = _limits(profile)[target]
    with _locked(path):
        raw = _read(path)
        _refuse_drift(path, raw, limit)
        entries = _parse(raw)
        if current not in entries:
            raise MemoryItemChanged(item_id)
        _write(path, [entry for entry in entries if entry != current])
