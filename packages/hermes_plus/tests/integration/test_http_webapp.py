"""Webapp window-ticket tests: one-use header ticket, same-origin exchange."""

from __future__ import annotations

import httpx
import pytest

from conftest import Sidecar

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _origin_headers(sidecar: Sidecar, ticket: str, origin: str | None = None) -> dict[str, str]:
    headers = {"X-Hermes-Window-Ticket": ticket}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def test_window_ticket_flow(sidecar: Sidecar):
    minted = httpx.post(
        f"{sidecar.url}/api/webapp/window-ticket", headers=_auth(sidecar), timeout=30)
    assert minted.status_code == 200, minted.text
    ticket = minted.json()["ticket"]
    assert ticket

    origin = f"http://127.0.0.1:{sidecar.port}"
    redeemed = httpx.post(
        f"{sidecar.url}/webapp/window-session",
        headers=_origin_headers(sidecar, ticket, origin),
        timeout=30,
    )
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json() == {"token": sidecar.token}
    assert redeemed.headers.get("cache-control") == "no-store"

    # One-use: the same ticket again is refused even from the same origin.
    again = httpx.post(
        f"{sidecar.url}/webapp/window-session",
        headers=_origin_headers(sidecar, ticket, origin),
        timeout=30,
    )
    assert again.status_code == 403


def test_window_ticket_requires_same_origin(sidecar: Sidecar):
    minted = httpx.post(
        f"{sidecar.url}/api/webapp/window-ticket", headers=_auth(sidecar), timeout=30)
    assert minted.status_code == 200
    ticket = minted.json()["ticket"]

    no_origin = httpx.post(
        f"{sidecar.url}/webapp/window-session",
        headers={"X-Hermes-Window-Ticket": ticket},
        timeout=30,
    )
    assert no_origin.status_code == 403

    cross = httpx.post(
        f"{sidecar.url}/webapp/window-session",
        headers=_origin_headers(sidecar, ticket, "https://evil.example.com"),
        timeout=30,
    )
    assert cross.status_code == 403

    # The refused exchange must not have burned the ticket for the real window.
    good = httpx.post(
        f"{sidecar.url}/webapp/window-session",
        headers=_origin_headers(sidecar, ticket, f"http://127.0.0.1:{sidecar.port}"),
        timeout=30,
    )
    assert good.status_code == 200


def test_window_ticket_requires_auth(sidecar: Sidecar):
    reply = httpx.post(f"{sidecar.url}/api/webapp/window-ticket", timeout=10)
    assert reply.status_code == 401


def test_handoff_pages_are_public(sidecar: Sidecar):
    launch = httpx.get(f"{sidecar.url}/webapp/launch", timeout=10)
    assert launch.status_code == 200
    assert "window-session" in launch.text
    assert "script-src 'nonce-" in launch.headers["content-security-policy"]

    window = httpx.get(f"{sidecar.url}/webapp/window", timeout=10)
    assert window.status_code == 200
    assert "BroadcastChannel" in window.text
