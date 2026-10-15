"""Share-link tests: create → public view without credentials → revoke → 404."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from conftest import HERMES_BIN, Sidecar, seed_stock_session

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _seed_session(sidecar: Sidecar) -> str:
    session_id = f"share-{uuid.uuid4().hex[:12]}"
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Please run the test suite"},
        {
            "role": "assistant",
            "content": "I ran it.",
            "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "run_tests", "arguments": "{\"suite\": \"secret-suite-name\"}"},
            }],
        },
        {"role": "tool", "content": "3 passed", "tool_call_id": "call-1"},
        {"role": "assistant", "content": "All 3 tests pass."},
    ]
    seed_stock_session(HERMES_BIN, sidecar.home, session_id, messages)
    return session_id


def test_share_round_trip(sidecar: Sidecar):
    session_id = _seed_session(sidecar)

    created = httpx.post(
        f"{sidecar.url}/api/share/create",
        json={"session_id": session_id},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    # Fork shape (fork web_routers/share.py:75-98).
    assert body["token"] and body["profile"] and body["path"].startswith("/share/view?t=")
    assert body["url"].startswith("http://127.0.0.1:")
    token = body["token"]

    # Public read path WITHOUT credentials: capability token only.
    view = httpx.get(
        f"{sidecar.url}/api/share/view",
        params={"t": token},
        timeout=30,
    )
    assert view.status_code == 200, view.text
    payload = view.json()
    assert set(payload) == {"title", "live", "model", "events", "next_after_id"}
    assert payload["title"].startswith("Shared demo")
    assert payload["next_after_id"] >= 4

    # The narrow event projection: user/assistant text + tool NAMES only.
    roles = [e["role"] for e in payload["events"]]
    assert roles == ["user", "assistant", "assistant"]
    raw = view.text
    assert "secret-suite-name" not in raw, "tool arguments must not leak through a share link"
    assert "run_tests" in raw
    assert "3 passed" not in raw, "tool results must not leak through a share link"
    assert payload["events"][0]["text"] == "Please run the test suite"

    assert view.headers.get("cache-control") == "no-store"
    assert view.headers.get("x-robots-tag") == "noindex"

    page = httpx.get(f"{sidecar.url}/share/view", params={"t": token}, timeout=30)
    assert page.status_code == 200
    assert "shared session" in page.text.lower()

    revoked = httpx.post(
        f"{sidecar.url}/api/share/revoke",
        json={"token": token},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}

    gone = httpx.get(f"{sidecar.url}/api/share/view", params={"t": token}, timeout=30)
    assert gone.status_code == 404
    page_gone = httpx.get(f"{sidecar.url}/share/view", params={"t": token}, timeout=30)
    assert page_gone.status_code == 404


def test_share_list_and_unknown_session(sidecar: Sidecar):
    session_id = _seed_session(sidecar)
    created = httpx.post(
        f"{sidecar.url}/api/share/create",
        json={"session_id": session_id, "ttl_seconds": 3600},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert created.status_code == 200
    listed = httpx.get(
        f"{sidecar.url}/api/share/list",
        params={"session_id": session_id},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert listed.status_code == 200
    shares = listed.json()["shares"]
    row = [s for s in shares if not s["revoked"]][-1]
    # Fork list row shape (fork share_grants.py list_shares).
    assert set(row) == {"token_preview", "created_at", "expires_at", "revoked", "expired"}
    assert row["expired"] is False

    missing = httpx.post(
        f"{sidecar.url}/api/share/create",
        json={"session_id": "no-such-session"},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "session not found"


def test_share_create_requires_auth(sidecar: Sidecar):
    reply = httpx.post(
        f"{sidecar.url}/api/share/create",
        json={"session_id": "whatever"},
        timeout=10,
    )
    assert reply.status_code == 401


def test_share_view_rejects_garbage_token(sidecar: Sidecar):
    reply = httpx.get(f"{sidecar.url}/api/share/view", params={"t": "short"}, timeout=10)
    assert reply.status_code == 404
