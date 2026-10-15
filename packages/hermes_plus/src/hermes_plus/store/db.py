"""plus.db: one SQLite file per profile, WAL mode, foreign keys on, numbered migrations."""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

DB_FILENAME = "plus.db"
_MIGRATION_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class SchemaTooNewError(RuntimeError):
    """plus.db was written by a newer hermes-plus than the one running now."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations() -> tuple[Migration, ...]:
    found: list[Migration] = []
    for entry in resources.files(__package__).joinpath("migrations").iterdir():
        match = _MIGRATION_FILE.match(entry.name)
        if match is not None:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort(key=lambda migration: migration.version)
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise RuntimeError("store migrations must be numbered 0001, 0002, ... without gaps")
    return tuple(found)


MIGRATIONS = load_migrations()
CODE_SCHEMA_VERSION = MIGRATIONS[-1].version


def _statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    if buffer.strip():
        raise RuntimeError("migration ends with an incomplete statement")


def _stored_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plus_meta'").fetchone()
    if has_meta is None:
        return 0
    row = conn.execute("SELECT value FROM plus_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row is not None else 0


class Store:
    """A single connection guarded by a lock; every transaction runs under that lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def schema_version(self) -> int:
        with self.read() as conn:
            return _stored_version(conn)

    def migrate(self) -> int:
        """Apply every migration newer than the stored version, atomically. Returns the new version."""
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = _stored_version(conn)
                if current > CODE_SCHEMA_VERSION:
                    raise SchemaTooNewError(
                        f"{self.path} has schema version {current}, but this hermes-plus only "
                        f"understands up to {CODE_SCHEMA_VERSION}; upgrade hermes-plus")
                for migration in MIGRATIONS:
                    if migration.version <= current:
                        continue
                    for statement in _statements(migration.sql):
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO plus_meta (key, value) VALUES ('schema_version', ?) "
                        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                        (str(migration.version),))
                    current = migration.version
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return current

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")


def open_store(plus_home: Path) -> Store:
    plus_home.mkdir(parents=True, exist_ok=True)
    store = Store(plus_home / DB_FILENAME)
    try:
        store.migrate()
    except BaseException:
        store.close()
        raise
    return store
