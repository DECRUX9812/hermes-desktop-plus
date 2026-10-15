"""Routine kind and condition for stock cron jobs; the schedule itself lives in the profile's cron store."""

from __future__ import annotations

from dataclasses import dataclass

from hermes_plus.store.db import Store


@dataclass(frozen=True)
class RoutineMeta:
    kind: str
    condition: str | None


class RoutineMetas:
    def __init__(self, store: Store) -> None:
        self._store = store

    def for_profile(self, profile: str) -> dict[str, RoutineMeta]:
        with self._store.read() as conn:
            rows = conn.execute(
                "SELECT job_id, kind, condition FROM routine_meta WHERE profile = ?", (profile,)).fetchall()
        return {row["job_id"]: RoutineMeta(row["kind"], row["condition"]) for row in rows}

    def put(self, profile: str, job_id: str, meta: RoutineMeta) -> None:
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO routine_meta (profile, job_id, kind, condition) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (profile, job_id) DO UPDATE SET kind = excluded.kind, condition = excluded.condition",
                (profile, job_id, meta.kind, meta.condition))

    def delete(self, profile: str, job_id: str) -> None:
        with self._store.write() as conn:
            conn.execute("DELETE FROM routine_meta WHERE profile = ? AND job_id = ?", (profile, job_id))
