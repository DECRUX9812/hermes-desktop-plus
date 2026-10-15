"""WS upgrade tickets: mint, single use, expiry, and the fail-closed paths."""

from __future__ import annotations

import time
from pathlib import Path

from hermes_plus.server.auth import WS_TICKET_TTL_SECONDS, FrontDoorAuth
from hermes_plus.settings import ServeSettings


def _auth(tmp_path: Path, **kwargs) -> FrontDoorAuth:
    return FrontDoorAuth(ServeSettings(ui_dir=tmp_path, **kwargs))


def _ws_scope(query: str = "") -> dict:
    return {
        "type": "websocket",
        "path": "/api/ws",
        "query_string": query.encode(),
        "headers": [],
        "client": ("127.0.0.1", 41234),
    }


def test_ticket_is_single_use(tmp_path: Path):
    auth = _auth(tmp_path)
    ticket = auth.mint_ws_ticket()
    assert auth.authenticated(_ws_scope(f"ticket={ticket}"), websocket=True)
    assert not auth.authenticated(_ws_scope(f"ticket={ticket}"), websocket=True)


def test_unknown_and_empty_tickets_fail_closed(tmp_path: Path):
    auth = _auth(tmp_path)
    assert not auth.authenticated(_ws_scope("ticket=not-a-ticket"), websocket=True)
    assert not auth.authenticated(_ws_scope("ticket="), websocket=True)
    assert not auth.authenticated(_ws_scope(), websocket=True)


def test_expired_ticket_is_rejected(tmp_path: Path):
    auth = _auth(tmp_path)
    ticket = auth.mint_ws_ticket()
    auth._ws_tickets[ticket] = time.monotonic() - 1
    assert not auth.authenticated(_ws_scope(f"ticket={ticket}"), websocket=True)


def test_the_session_token_still_authenticates_an_upgrade(tmp_path: Path):
    auth = _auth(tmp_path)
    assert auth.authenticated(_ws_scope(f"token={auth.token}"), websocket=True)
    assert not auth.authenticated(_ws_scope("token=wrong"), websocket=True)


def test_minting_prunes_expired_tickets(tmp_path: Path):
    auth = _auth(tmp_path)
    stale = auth.mint_ws_ticket()
    auth._ws_tickets[stale] = time.monotonic() - 1
    fresh = auth.mint_ws_ticket()
    assert stale not in auth._ws_tickets
    assert fresh in auth._ws_tickets


def test_ttl_is_short(tmp_path: Path):
    assert 0 < WS_TICKET_TTL_SECONDS <= 60
