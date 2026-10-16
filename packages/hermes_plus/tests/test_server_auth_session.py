"""Password-mode browser sessions: a persistent signing key, not the process token.

The key lives at `<plus_home>/session.key` (0600) and the cookie's MAC is bound
to the password digest, so a restart keeps browsers logged in while a password
change does not. Real files, no mocks.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from hermes_plus.server.auth import (
    COOKIE_NAME,
    SESSION_KEY_BYTES,
    FrontDoorAuth,
    load_or_create_session_key,
    session_key_path,
)
from hermes_plus.settings import ServeSettings

DEMO_PASSWORD = "demo-password-s36"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own HERMES_HOME: never the developer's real one."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.delenv("HERMES_PLUS_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_PLUS_PASSWORD", raising=False)


def _scope(scheme: str = "http") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "scheme": scheme,
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 5000),
    }


def _password_auth(tmp_path: Path, password: str = DEMO_PASSWORD) -> FrontDoorAuth:
    password_file = tmp_path / "password.txt"
    password_file.write_text(password, encoding="utf-8")
    return FrontDoorAuth(ServeSettings(auth="password", password_file=password_file))


def _cookie_header(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"cookie", f"{COOKIE_NAME}={value}".encode())]


def _cookie_value(header: bytes) -> str:
    return header.decode().split(";", 1)[0].split("=", 1)[1]


def test_key_path_sits_in_plus_home(tmp_path: Path) -> None:
    assert session_key_path() == tmp_path / "hermes-home" / "hermes-plus" / "session.key"


def test_password_session_survives_a_restart(tmp_path: Path) -> None:
    before = _password_auth(tmp_path)
    cookie = _cookie_value(before.cookie_header(_scope()))

    # A restarted sidecar: a new process token, the same profile on disk.
    after = _password_auth(tmp_path)
    assert after.token != before.token
    assert cookie != after.token and before.token not in cookie
    assert after.authenticated(_scope() | {"headers": _cookie_header(cookie)})


def test_cookie_is_stable_across_instances(tmp_path: Path) -> None:
    first = _password_auth(tmp_path).session_cookie_value()
    second = _password_auth(tmp_path).session_cookie_value()
    assert first == second
    assert first.startswith("v1.")


def test_key_file_is_created_0600_on_first_use(tmp_path: Path) -> None:
    key_path = tmp_path / "hermes-home" / "hermes-plus" / "session.key"
    auth = _password_auth(tmp_path)
    assert not key_path.exists(), "no key is written before a session is minted"

    auth.cookie_header(_scope())
    assert key_path.is_file()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert len(key_path.read_bytes()) == SESSION_KEY_BYTES
    assert auth.session_key() == key_path.read_bytes()


def test_token_mode_never_writes_a_key(tmp_path: Path) -> None:
    auth = FrontDoorAuth(ServeSettings(auth="token"))
    cookie = auth.cookie_header(_scope()).decode()
    assert f"{COOKIE_NAME}={auth.token}" in cookie
    assert not (tmp_path / "hermes-home" / "hermes-plus" / "session.key").exists()


def test_changing_the_password_invalidates_old_sessions(tmp_path: Path) -> None:
    cookie = _cookie_value(_password_auth(tmp_path).cookie_header(_scope()))

    changed = _password_auth(tmp_path, "a-different-demo-password")
    assert not changed.authenticated(_scope() | {"headers": _cookie_header(cookie)})
    # The new password's own cookie works, and the key file was not rotated.
    fresh = _cookie_value(changed.cookie_header(_scope()))
    assert changed.authenticated(_scope() | {"headers": _cookie_header(fresh)})


def test_foreign_and_tampered_session_cookies_are_refused(tmp_path: Path) -> None:
    auth = _password_auth(tmp_path)
    good = auth.session_cookie_value()
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")

    for bad in ("", tampered, "v2." + good[3:], "v1.", "v1.zz", "v1.00", good[:-2]):
        assert not auth.valid_session_cookie(bad), bad
        assert not auth.authenticated(_scope() | {"headers": _cookie_header(bad)}), bad
    # The signed value authenticates the browser that holds it.
    assert auth.authenticated(_scope() | {"headers": _cookie_header(good)})

    # A token-mode sidecar does not accept a password session cookie.
    token_mode = FrontDoorAuth(ServeSettings(auth="token"))
    assert not token_mode.authenticated(_scope() | {"headers": _cookie_header(good)})


def test_process_token_cookie_still_authenticates(tmp_path: Path) -> None:
    """`HERMES_PLUS_TOKEN` keeps working as it did: header, cookie and query."""
    auth = _password_auth(tmp_path)
    assert auth.has_valid_token(auth.token)
    assert auth.authenticated(_scope() | {"headers": _cookie_header(auth.token)})


def test_cookie_flags_are_unchanged_in_password_mode(tmp_path: Path) -> None:
    auth = _password_auth(tmp_path)
    plain = auth.cookie_header(_scope()).decode()
    assert "HttpOnly" in plain and "SameSite=Lax" in plain and "Secure" not in plain
    assert "Max-Age" not in plain and "Expires" not in plain, "still a browser-session cookie"
    assert "Secure" in auth.cookie_header(_scope("https")).decode()


def test_a_corrupt_key_file_is_replaced(tmp_path: Path) -> None:
    key_path = tmp_path / "hermes-home" / "hermes-plus" / "session.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_bytes(b"truncated")

    auth = _password_auth(tmp_path)
    value = auth.session_cookie_value()
    assert len(key_path.read_bytes()) == SESSION_KEY_BYTES
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert auth.valid_session_cookie(value)


def test_load_or_create_returns_the_existing_key(tmp_path: Path) -> None:
    key_path = tmp_path / "keep" / "session.key"
    first = load_or_create_session_key(key_path)
    second = load_or_create_session_key(key_path)
    assert first == second and len(first) == SESSION_KEY_BYTES
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert [entry.name for entry in key_path.parent.iterdir()] == ["session.key"]
