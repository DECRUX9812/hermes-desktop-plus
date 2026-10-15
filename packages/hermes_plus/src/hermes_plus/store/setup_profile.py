"""The setup profile this install created for first-run onboarding, if one exists."""

from __future__ import annotations

from datetime import UTC, datetime

from hermes_plus.store.db import Store


class SetupProfiles:
    def __init__(self, store: Store) -> None:
        self._store = store

    def name(self) -> str | None:
        with self._store.read() as conn:
            row = conn.execute("SELECT name FROM setup_profile WHERE id = 1").fetchone()
        return row["name"] if row is not None else None

    def record(self, name: str) -> None:
        created_at = datetime.now(UTC).timestamp()
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO setup_profile (id, name, created_at) VALUES (1, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET name = excluded.name, created_at = excluded.created_at",
                (name, created_at))

    def clear(self) -> None:
        with self._store.write() as conn:
            conn.execute("DELETE FROM setup_profile WHERE id = 1")
