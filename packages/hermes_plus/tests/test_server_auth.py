"""Front-door auth: token, cookie, password verification, and proxy headers."""

from __future__ import annotations

import time
from pathlib import Path

from hermes_plus.server.auth import COOKIE_NAME, FrontDoorAuth
from hermes_plus.settings import ServeSettings


def _scope(headers: list[tuple[bytes, bytes]] | None = None, *, scheme: str = "http") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "scheme": scheme,
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 5000),
    }


def test_token_header_and_cookie_authenticate():
    auth = FrontDoorAuth(ServeSettings(auth="token"))
    assert auth.authenticated(_scope([(b"x-hermes-session-token", auth.token.encode())]))
    assert auth.authenticated(_scope([(b"cookie", f"{COOKIE_NAME}={auth.token}".encode())]))
    assert not auth.authenticated(_scope([(b"x-hermes-session-token", b"wrong")]))
    assert not auth.authenticated(_scope())


def test_websocket_accepts_query_token():
    auth = FrontDoorAuth(ServeSettings(auth="token"))
    scope = _scope()
    scope["type"] = "websocket"
    scope["query_string"] = f"token={auth.token}".encode()
    assert auth.authenticated(scope, websocket=True)
    scope["query_string"] = b"token=nope"
    assert not auth.authenticated(scope, websocket=True)


def test_auth_none_allows_everything():
    auth = FrontDoorAuth(ServeSettings(auth="none"))
    assert auth.authenticated(_scope())


def test_cookie_flags_follow_request_scheme():
    auth = FrontDoorAuth(ServeSettings(auth="token"))
    plain = auth.cookie_header(_scope()).decode()
    assert "HttpOnly" in plain and "SameSite=Lax" in plain and "Secure" not in plain
    secure = auth.cookie_header(_scope(scheme="https")).decode()
    assert "Secure" in secure


def test_cookie_secure_honours_trusted_proxy_headers():
    settings = ServeSettings(auth="token", trust_proxy_headers=True)
    auth = FrontDoorAuth(settings)
    scope = _scope([(b"x-forwarded-proto", b"https")])
    assert "Secure" in auth.cookie_header(scope).decode()
    untrusted = FrontDoorAuth(ServeSettings(auth="token", trust_proxy_headers=False))
    assert "Secure" not in untrusted.cookie_header(scope).decode()


def test_password_verify_is_constant_time_and_bounded(tmp_path: Path, monkeypatch):
    password_file = tmp_path / "password.txt"
    password_file.write_text("demo-password\n")
    monkeypatch.delenv("HERMES_PLUS_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_PLUS_TOKEN", raising=False)
    auth = FrontDoorAuth(ServeSettings(auth="password", password_file=password_file))
    assert auth.verify_password("demo-password")
    assert not auth.verify_password("demo-passwore")
    assert not auth.verify_password("")


def test_password_attempts_are_rate_limited(tmp_path: Path, monkeypatch):
    password_file = tmp_path / "password.txt"
    password_file.write_text("demo-password")
    monkeypatch.delenv("HERMES_PLUS_PASSWORD", raising=False)
    auth = FrontDoorAuth(ServeSettings(auth="password", password_file=password_file))
    scope = _scope()
    for _ in range(5):
        assert auth.consume_password_attempt(scope)
    assert not auth.consume_password_attempt(scope)
    for attempts in auth._attempts.values():
        attempts.clear()
    assert auth.consume_password_attempt(scope)


def test_forwarded_for_only_when_trusted():
    headers = [(b"x-forwarded-for", b"198.51.100.7, 10.0.0.1")]
    trusted = FrontDoorAuth(ServeSettings(trust_proxy_headers=True))
    assert trusted.client_address(_scope(headers)) == "198.51.100.7"
    plain = FrontDoorAuth(ServeSettings(trust_proxy_headers=False))
    assert plain.client_address(_scope(headers)) == "127.0.0.1"


def test_env_token_is_used(monkeypatch):
    monkeypatch.setenv("HERMES_PLUS_TOKEN", "fixed-test-token")
    auth = FrontDoorAuth(ServeSettings(auth="token"))
    assert auth.token == "fixed-test-token"
    assert auth.authenticated(_scope([(b"x-hermes-session-token", b"fixed-test-token")]))


def test_attempt_window_expires(tmp_path: Path, monkeypatch):
    password_file = tmp_path / "password.txt"
    password_file.write_text("demo-password")
    monkeypatch.delenv("HERMES_PLUS_PASSWORD", raising=False)
    auth = FrontDoorAuth(ServeSettings(auth="password", password_file=password_file))
    scope = _scope()
    auth._attempts["127.0.0.1"].append(time.monotonic() - 120)
    assert auth.consume_password_attempt(scope)
