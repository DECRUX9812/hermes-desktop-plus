"""Mobile pairing-code storage in plus.db, so a minted code survives restarts.

Codes are single-use, short-lived, and rate-limited on failure (the limiter
counters stay in memory, like the fork). Only the code's SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from hermes_plus.store import Store

TTL_SECONDS = 600.0
CODE_DIGITS = 6
MAX_OUTSTANDING = 8


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def mint(store: Store) -> dict[str, int | str]:
    """Mint one code; prune expired rows and evict the oldest beyond the cap."""
    with store.write() as conn:
        now = time.time()
        conn.execute("DELETE FROM mobile_pairings WHERE expires_at <= ?", (now,))
        outstanding = conn.execute("SELECT COUNT(*) AS n FROM mobile_pairings").fetchone()["n"]
        if outstanding >= MAX_OUTSTANDING:
            conn.execute(
                "DELETE FROM mobile_pairings WHERE id IN ("
                "SELECT id FROM mobile_pairings ORDER BY created_at LIMIT ?)",
                (outstanding - MAX_OUTSTANDING + 1,),
            )
        code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
        conn.execute(
            "INSERT INTO mobile_pairings (code_hash, created_at, expires_at) VALUES (?, ?, ?)",
            (_code_hash(code), now, now + TTL_SECONDS),
        )
    return {"code": code, "expires_in": int(TTL_SECONDS), "page": "/mobile"}


def redeem(store: Store, code: str) -> bool:
    """Spend a valid, unexpired code (single use, valid or not)."""
    with store.write() as conn:
        now = time.time()
        row = conn.execute(
            "SELECT id FROM mobile_pairings WHERE code_hash = ? AND paired_at IS NULL AND expires_at > ?",
            (_code_hash(code), now),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE mobile_pairings SET paired_at = ? WHERE id = ?", (now, row["id"])
        )
        conn.execute("DELETE FROM mobile_pairings WHERE paired_at IS NOT NULL AND paired_at < ?", (now - 3600,))
    return True
