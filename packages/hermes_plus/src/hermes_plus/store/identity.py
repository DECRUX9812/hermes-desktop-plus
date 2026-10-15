"""Bot identity per profile: display name, role, instructions and model pin, with a revision for CAS."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from hermes_plus.store.db import Store

IDENTITY_FIELDS = ("display_name", "role", "instructions", "model_provider", "model_default")


class RevisionConflictError(Exception):
    """The caller's expected revision is stale."""


@dataclass(frozen=True)
class Identity:
    profile: str
    display_name: str
    role: str
    instructions: str
    model_provider: str
    model_default: str
    revision: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "display_name": self.display_name,
            "role": self.role,
            "instructions": self.instructions,
            "model": {"provider": self.model_provider, "default": self.model_default},
            "revision": self.revision,
        }


def _from_row(profile: str, row: sqlite3.Row | None) -> Identity:
    if row is None:
        return Identity(profile, "", "", "", "", "", 0)
    return Identity(profile, row["display_name"], row["role"], row["instructions"],
                    row["model_provider"], row["model_default"], row["revision"])


class BotIdentities:
    def __init__(self, store: Store) -> None:
        self._store = store

    def get(self, profile: str) -> Identity:
        with self._store.read() as conn:
            row = conn.execute("SELECT * FROM bot_identities WHERE profile = ?", (profile,)).fetchone()
        return _from_row(profile, row)

    def update(self, profile: str, changes: dict[str, str], *, expected_revision: int | None) -> Identity:
        """Apply the given fields. A display-name or role change bumps the revision, as the fork's UI meta did."""
        unknown = set(changes) - set(IDENTITY_FIELDS)
        if unknown:
            raise ValueError(f"unknown identity fields: {sorted(unknown)}")
        with self._store.write() as conn:
            row = conn.execute("SELECT * FROM bot_identities WHERE profile = ?", (profile,)).fetchone()
            current = _from_row(profile, row)
            if expected_revision is not None and expected_revision != current.revision:
                raise RevisionConflictError(profile)
            merged = {name: changes.get(name, getattr(current, name)) for name in IDENTITY_FIELDS}
            bumps = any(name in changes for name in ("display_name", "role"))
            revision = current.revision + 1 if bumps else current.revision
            conn.execute(
                "INSERT INTO bot_identities (profile, display_name, role, instructions, model_provider, "
                "model_default, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (profile) DO UPDATE SET display_name = excluded.display_name, "
                "role = excluded.role, instructions = excluded.instructions, "
                "model_provider = excluded.model_provider, model_default = excluded.model_default, "
                "revision = excluded.revision, updated_at = excluded.updated_at",
                (profile, merged["display_name"], merged["role"], merged["instructions"],
                 merged["model_provider"], merged["model_default"], revision, time.time()))
        return Identity(profile, merged["display_name"], merged["role"], merged["instructions"],
                        merged["model_provider"], merged["model_default"], revision)
