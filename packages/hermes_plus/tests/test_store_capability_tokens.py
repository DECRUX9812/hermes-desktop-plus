"""Capability tokens in plus.db: entropy, single use where claimed, expiry.

Real SQLite files (the `store` fixture), no mocks. The WebSocket and file
tickets are in-memory and covered by the integration suite; these are the two
that outlive a restart.
"""

from __future__ import annotations

import string
import time

from hermes_plus.store import Store
from hermes_plus.store import mobile as pairings
from hermes_plus.store import share as grants

_URLSAFE = set(string.ascii_letters + string.digits + "-_")


def test_share_tokens_are_256_bit_and_only_the_hash_is_stored(store: Store):
    minted = grants.mint(store, "session-demo")
    token = minted["token"]
    assert len(token) == 43 and set(token) <= _URLSAFE
    assert minted["expires_at"] is None

    with store.read() as conn:
        stored = conn.execute("SELECT token_hash, token_preview FROM share_grants").fetchall()
    assert len(stored) == 1
    assert stored[0]["token_hash"] != token
    assert len(stored[0]["token_hash"]) == 64  # sha256 hex
    assert token.endswith(stored[0]["token_preview"])


def test_share_tokens_are_unique_and_resolve_to_their_own_session(store: Store):
    first = grants.mint(store, "session-one")["token"]
    second = grants.mint(store, "session-two")["token"]
    assert first != second
    assert grants.resolve(store, first) == {"session_id": "session-one"}
    assert grants.resolve(store, second) == {"session_id": "session-two"}
    assert grants.resolve(store, "short") is None
    assert grants.resolve(store, "x" * 43) is None


def test_a_revoked_or_expired_share_token_resolves_to_nothing(store: Store):
    token = grants.mint(store, "session-demo", ttl_seconds=60)["token"]
    assert grants.resolve(store, token) is not None
    assert grants.revoke(store, token) is True
    assert grants.resolve(store, token) is None
    assert grants.revoke(store, token) is False

    short_lived = grants.mint(store, "session-demo", ttl_seconds=-1)["token"]
    # ttl_seconds is clamped to a 60 s floor, so expire it by hand.
    with store.write() as conn:
        conn.execute("UPDATE share_grants SET expires_at = ? WHERE token_hash IS NOT NULL",
                     (time.time() - 1,))
    assert grants.resolve(store, short_lived) is None
    rows = grants.list_for(store, "session-demo")
    assert any(row["expired"] for row in rows)


def test_pairing_codes_are_single_use_six_digits(store: Store):
    minted = pairings.mint(store)
    code = minted["code"]
    assert len(code) == 6 and code.isdigit()
    assert minted["expires_in"] == int(pairings.TTL_SECONDS)
    with store.read() as conn:
        stored = conn.execute("SELECT code_hash FROM mobile_pairings").fetchall()
    assert len(stored) == 1 and stored[0]["code_hash"] != code

    assert pairings.redeem(store, code) is True
    assert pairings.redeem(store, code) is False, "a pairing code is single-use"
    assert pairings.redeem(store, "000000") is False


def test_expired_pairing_codes_are_refused(store: Store):
    code = pairings.mint(store)["code"]
    with store.write() as conn:
        conn.execute("UPDATE mobile_pairings SET expires_at = ?", (time.time() - 1,))
    assert pairings.redeem(store, code) is False


def test_outstanding_pairing_codes_are_capped(store: Store):
    for _ in range(pairings.MAX_OUTSTANDING + 3):
        pairings.mint(store)
    with store.read() as conn:
        outstanding = conn.execute("SELECT COUNT(*) AS n FROM mobile_pairings").fetchone()["n"]
    assert outstanding == pairings.MAX_OUTSTANDING
