"""First-run onboarding singleton: intro seen flag and failed setup starts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_plus.store.db import Store

MAX_FAILED_STARTS = 3


@dataclass(frozen=True)
class OnboardingState:
    intro: str
    failed_starts: int
    completed_at: str | None

    def to_wire(self, *, eligible: bool, profile: str | None) -> dict[str, Any]:
        return {
            "eligible": eligible,
            "profile": profile,
            "intro": self.intro,
            "failed_starts": self.failed_starts,
            "completed_at": self.completed_at,
        }


_FRESH = OnboardingState("unseen", 0, None)


def _from_row(row) -> OnboardingState:
    return OnboardingState(row["intro"], row["failed_starts"], row["completed_at"])


class Onboarding:
    def __init__(self, store: Store) -> None:
        self._store = store

    def state(self) -> OnboardingState:
        with self._store.read() as conn:
            row = conn.execute("SELECT intro, failed_starts, completed_at FROM onboarding_state WHERE id = 1").fetchone()
        return _from_row(row) if row is not None else _FRESH

    def mark_seen(self) -> OnboardingState:
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO onboarding_state (id, intro, failed_starts) VALUES (1, 'seen', 0) "
                "ON CONFLICT (id) DO UPDATE SET intro = 'seen'")
            row = conn.execute("SELECT intro, failed_starts, completed_at FROM onboarding_state WHERE id = 1").fetchone()
        return _from_row(row)

    def record_failed_start(self) -> OnboardingState:
        with self._store.write() as conn:
            row = conn.execute("SELECT failed_starts FROM onboarding_state WHERE id = 1").fetchone()
            failed = min((row["failed_starts"] if row is not None else 0) + 1, MAX_FAILED_STARTS)
            intro = "seen" if failed == MAX_FAILED_STARTS else "unseen"
            conn.execute(
                "INSERT INTO onboarding_state (id, intro, failed_starts) VALUES (1, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET failed_starts = excluded.failed_starts, "
                "intro = CASE WHEN excluded.failed_starts = ? THEN 'seen' ELSE onboarding_state.intro END",
                (intro, failed, MAX_FAILED_STARTS))
            row = conn.execute("SELECT intro, failed_starts, completed_at FROM onboarding_state WHERE id = 1").fetchone()
        return _from_row(row)
