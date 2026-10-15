"""Mobile pairing tests: mint (authed) → pair (public) → paired calls; the
pairing code must survive a real sidecar restart (plus.db persistence)."""

from __future__ import annotations

import httpx
import pytest

from conftest import Sidecar

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _mint(sidecar: Sidecar) -> dict:
    reply = httpx.post(f"{sidecar.url}/api/mobile/pairing", headers=_auth(sidecar), timeout=30)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert set(body) == {"code", "expires_in", "page"}
    assert body["page"] == "/mobile"
    assert len(body["code"]) == 6 and body["expires_in"] == 600
    return body


def _pair(sidecar: Sidecar, code: str) -> httpx.Response:
    return httpx.post(f"{sidecar.url}/api/mobile/pair", json={"code": code}, timeout=30)


def test_pairing_happy_path(sidecar: Sidecar):
    minted = _mint(sidecar)
    paired = _pair(sidecar, minted["code"])
    assert paired.status_code == 200, paired.text
    assert paired.json() == {"token": sidecar.token}

    # The paired phone calls with Bearer auth from here on.
    overview = httpx.get(
        f"{sidecar.url}/api/mobile/overview",
        headers={"Authorization": f"Bearer {sidecar.token}"},
        timeout=30,
    )
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert set(body) == {"sessions", "generated_at"}
    assert isinstance(body["sessions"], list)

    # The code was single-use.
    reused = _pair(sidecar, minted["code"])
    assert reused.status_code == 403


def test_pairing_survives_restart(sidecar: Sidecar):
    minted = _mint(sidecar)
    sidecar.restart()
    assert httpx.get(f"{sidecar.url}/healthz", timeout=10).status_code == 200

    paired = _pair(sidecar, minted["code"])
    assert paired.status_code == 200, paired.text
    assert paired.json() == {"token": sidecar.token}


def test_pairing_rejects_bad_code_and_page_is_public(sidecar: Sidecar):
    bad = _pair(sidecar, "000000" if _pair(sidecar, "999999").status_code == 403 else "111111")
    assert bad.status_code == 403

    page = httpx.get(f"{sidecar.url}/mobile", timeout=10)
    assert page.status_code == 200
    assert "api/mobile/overview" in page.text

    # Minting requires auth; pairing alone never mints.
    refused = httpx.post(f"{sidecar.url}/api/mobile/pairing", timeout=10)
    assert refused.status_code == 401
