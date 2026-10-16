"""Password-mode browser sessions across a real sidecar restart (no mocks).

A real `hermes-plus serve` subprocess over real stock Hermes in a throwaway
HERMES_HOME: log in with a password, restart the process, the same cookie still
gets 200; change the password, restart, it is refused. The signing key is a
0600 file under plus_home, and token mode is unchanged.
"""

from __future__ import annotations

import stat
from pathlib import Path

import httpx
import pytest

from conftest import Sidecar, make_sidecar

pytestmark = pytest.mark.integration

PASSWORD = "demo-password-s36"
COOKIE = "hermes_plus_session"
# The path the session cookie is bound to, from settings.py: <HERMES_HOME>/hermes-plus.
SESSION_KEY = Path("hermes-plus") / "session.key"


def _password_sidecar(tmp_path: Path) -> Sidecar:
    return make_sidecar(
        tmp_path,
        env_extra={"HERMES_PLUS_AUTH": "password", "HERMES_PLUS_PASSWORD": PASSWORD},
    )


def _login(server: Sidecar, password: str, client: httpx.Client) -> httpx.Response:
    return client.post(f"{server.url}/login", data={"password": password})


def test_a_password_session_survives_a_sidecar_restart(tmp_path: Path):
    server = _password_sidecar(tmp_path)
    try:
        with httpx.Client(follow_redirects=False, timeout=30) as client:
            # Unauthenticated API callers are sent to the login form.
            assert client.get(f"{server.url}/api/plus/info").status_code == 303

            reply = _login(server, PASSWORD, client)
            assert reply.status_code == 303, reply.text
            header = reply.headers["set-cookie"]
            assert "HttpOnly" in header and "SameSite=Lax" in header
            assert PASSWORD not in header, "the password never reaches the cookie"
            assert server.token not in header, "the process token is not the session"
            session = client.cookies.get(COOKIE)
            assert session, header

            first = client.get(f"{server.url}/api/plus/info")
            assert first.status_code == 200, first.text

            server.restart()

            # Same cookie jar, new process: still authenticated, no re-login.
            after = client.get(f"{server.url}/api/plus/info")
            assert after.status_code == 200, after.text
            assert after.json()["version"] == first.json()["version"]
            assert client.cookies.get(COOKIE) == session

            # The restarted sidecar really is a new process with a new token.
            assert httpx.get(f"{server.url}/healthz", timeout=10).status_code == 200
    finally:
        server.stop()


def test_changing_the_password_refuses_the_old_cookie(tmp_path: Path):
    server = _password_sidecar(tmp_path)
    try:
        with httpx.Client(follow_redirects=False, timeout=30) as client:
            assert _login(server, PASSWORD, client).status_code == 303
            assert client.get(f"{server.url}/api/plus/info").status_code == 200

            server.env_extra["HERMES_PLUS_PASSWORD"] = "a-different-demo-password"
            server.restart()

            refused = client.get(f"{server.url}/api/plus/info")
            assert refused.status_code == 303, refused.text
            assert refused.headers["location"].endswith("/login")

            assert _login(server, "a-different-demo-password", client).status_code == 303
            assert client.get(f"{server.url}/api/plus/info").status_code == 200
    finally:
        server.stop()


def test_the_session_key_is_a_0600_file_in_plus_home(tmp_path: Path):
    server = _password_sidecar(tmp_path)
    key_path = server.home / SESSION_KEY
    try:
        assert not key_path.exists(), "no key is written before the first login"

        with httpx.Client(follow_redirects=False, timeout=30) as client:
            assert _login(server, PASSWORD, client).status_code == 303
            assert client.get(f"{server.url}/api/plus/info").status_code == 200

        assert key_path.is_file()
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        assert len(key_path.read_bytes()) == 32
    finally:
        server.stop()


def test_token_mode_is_unchanged(sidecar: Sidecar):
    """Token mode still hands the browser the token, and writes no key file."""
    with httpx.Client(follow_redirects=False, timeout=30) as client:
        landing = client.get(f"{sidecar.url}/", params={"token": sidecar.token})
        assert landing.status_code == 303
        assert f"{COOKIE}={sidecar.token}" in landing.headers["set-cookie"]
        assert client.get(f"{sidecar.url}/api/plus/info").status_code == 200
    assert not (sidecar.home / SESSION_KEY).exists()
