"""Share-grant storage in plus.db: a port of fork hermes_cli/share_grants.py.

A grant is a capability token bound to one session_id. The raw token is
returned exactly once, at mint; the DB keeps only its SHA-256 plus a display
suffix. Shapes follow the fork: ``{"token", "expires_at"}`` at mint,
``{"token_preview", "created_at", "expires_at", "revoked", "expired"}`` on
list, and ``{"session_id"}`` on resolve.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from hermes_plus.store import Store

MAX_ACTIVE_PER_SESSION = 32
_PREVIEW_CHARS = 6
_MIN_TOKEN_CHARS = 32


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _active(grant: dict[str, Any], now: float) -> bool:
    return not grant.get("revoked") and (grant.get("expires_at") is None or grant["expires_at"] > now)


def mint(store: Store, session_id: str, ttl_seconds: float | None = None) -> dict[str, Any]:
    """Mint a grant; ValueError when the per-session cap is reached."""
    with store.write() as conn:
        now = time.time()
        live = conn.execute(
            "SELECT COUNT(*) AS n FROM share_grants "
            "WHERE session_id = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)",
            (session_id, now),
        ).fetchone()["n"]
        if live >= MAX_ACTIVE_PER_SESSION:
            raise ValueError(f"share cap reached for this session ({MAX_ACTIVE_PER_SESSION} active links)")
        token = secrets.token_urlsafe(32)
        expires_at = None if ttl_seconds is None else now + max(60.0, float(ttl_seconds))
        conn.execute(
            "INSERT INTO share_grants (session_id, token_hash, token_preview, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, _token_hash(token), token[-_PREVIEW_CHARS:], now, expires_at),
        )
    return {"token": token, "expires_at": expires_at}


def revoke(store: Store, token: str) -> bool:
    with store.write() as conn:
        cursor = conn.execute(
            "UPDATE share_grants SET revoked = 1 WHERE token_hash = ? AND revoked = 0",
            (_token_hash(token),),
        )
        return cursor.rowcount > 0


def list_for(store: Store, session_id: str) -> list[dict[str, Any]]:
    now = time.time()
    with store.read() as conn:
        rows = conn.execute(
            "SELECT token_preview, created_at, expires_at, revoked FROM share_grants "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    grants = [dict(row) for row in rows]
    for grant in grants:
        grant["revoked"] = bool(grant["revoked"])
        grant["expired"] = not _active(grant, now) and not grant["revoked"]
    return grants


def resolve(store: Store, token: str) -> dict[str, Any] | None:
    """``{"session_id"}`` when the token names a live grant, else None."""
    if not token or len(token) < _MIN_TOKEN_CHARS:
        return None
    now = time.time()
    with store.read() as conn:
        row = conn.execute(
            "SELECT session_id, revoked, expires_at FROM share_grants WHERE token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
    if row is None:
        return None
    grant = dict(row)
    return {"session_id": grant["session_id"]} if _active(grant, now) else None
