from __future__ import annotations

import sqlite3
import threading

import pytest

from hermes_plus.store import DB_FILENAME, SchemaTooNewError, Store, open_store


def test_open_store_creates_wal_database_with_foreign_keys_on(tmp_path):
    store = open_store(tmp_path / "home")
    try:
        assert (tmp_path / "home" / DB_FILENAME).is_file()
        assert store.schema_version() == 3
        with store.read() as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        store.close()


def test_migrate_is_idempotent(tmp_path):
    store = open_store(tmp_path / "home")
    try:
        assert store.migrate() == 3
        assert store.migrate() == 3
        with store.read() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM plus_meta WHERE key = 'schema_version'").fetchone()[0]
        assert rows == 1
    finally:
        store.close()


def test_database_newer_than_code_is_refused(tmp_path):
    home = tmp_path / "home"
    store = open_store(home)
    with store.write() as conn:
        conn.execute("UPDATE plus_meta SET value = '99' WHERE key = 'schema_version'")
    store.close()

    newer = Store(home / DB_FILENAME)
    try:
        with pytest.raises(SchemaTooNewError):
            newer.migrate()
    finally:
        newer.close()


def test_failed_write_rolls_back(store):
    with pytest.raises(RuntimeError):
        with store.write() as conn:
            conn.execute("INSERT INTO plus_meta (key, value) VALUES ('probe', 'x')")
            raise RuntimeError("boom")
    with store.read() as conn:
        assert conn.execute("SELECT 1 FROM plus_meta WHERE key = 'probe'").fetchone() is None


def test_foreign_keys_are_enforced(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store.write() as conn:
            conn.execute(
                "INSERT INTO team_goal_tasks (team_id, goal_id, position, task_id) VALUES ('team_x', 'goal_x', 0, 't')")


def test_concurrent_writers_do_not_lose_rows(store):
    def add(n: int) -> None:
        with store.write() as conn:
            conn.execute("INSERT INTO plus_meta (key, value) VALUES (?, 'v')", (f"k{n}",))

    threads = [threading.Thread(target=add, args=(n,)) for n in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with store.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM plus_meta WHERE key LIKE 'k%'").fetchone()[0]
    assert count == 32
