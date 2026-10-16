"""Front-door and surface policy units: the Origin check, exact public paths,
the address the login limiter counts against, and where a host shell is
allowed. No mocks: the policy is pure and the scopes are the ASGI scopes the
server really sees."""

from __future__ import annotations

from hermes_plus.http import host_terminal
from hermes_plus.server import auth as auth_module
from hermes_plus.server.auth import ALLOWED_ORIGINS_ENV, FrontDoorAuth
from hermes_plus.settings import ServeSettings


def _scope(
    *,
    method: str = "POST",
    path: str = "/api/plus/info",
    kind: str = "http",
    host: str = "127.0.0.1:9129",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict:
    raw = [(b"host", host.encode())] + (headers or [])
    return {
        "type": kind,
        "method": method,
        "path": path,
        "scheme": "http",
        "query_string": b"",
        "headers": raw,
        "client": ("127.0.0.1", 5000),
    }


def _auth(**kwargs) -> FrontDoorAuth:
    return FrontDoorAuth(ServeSettings(auth="token", **kwargs))


def test_same_origin_is_accepted():
    auth = _auth()
    scope = _scope(headers=[(b"origin", b"http://127.0.0.1:9129")])
    assert auth.cross_origin(scope) is False
    assert auth.origin_refused(scope) is False


def test_another_port_on_the_same_host_is_cross_origin():
    """SameSite=Lax does not stop this: another localhost port is same-site."""
    auth = _auth()
    for origin in (b"http://127.0.0.1:8000", b"http://localhost:9129",
                   b"http://127.0.0.1:9129.evil.example.com"):
        scope = _scope(headers=[(b"origin", origin)])
        assert auth.cross_origin(scope) is True, origin
        assert auth.origin_refused(scope) is True, origin


def test_null_and_malformed_origins_are_cross_origin():
    auth = _auth()
    for origin in (b"null", b"file:///tmp/demo.html", b"chrome-extension://abcdef",
                   b"http://127.0.0.1:9129/extra", b"http://user@127.0.0.1:9129"):
        assert auth.cross_origin(_scope(headers=[(b"origin", origin)])) is True, origin


def test_a_missing_origin_is_never_refused():
    """Non-browser clients (curl, the bridge plugin, the CLI) send no Origin."""
    auth = _auth()
    assert auth.cross_origin(_scope()) is False
    assert auth.origin_refused(_scope()) is False


def test_only_websockets_and_unsafe_methods_are_checked():
    auth = _auth()
    for method in ("GET", "HEAD", "OPTIONS"):
        scope = _scope(method=method, headers=[(b"origin", b"http://127.0.0.1:8000")])
        assert auth.origin_refused(scope) is False, method
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        scope = _scope(method=method, headers=[(b"origin", b"http://127.0.0.1:8000")])
        assert auth.origin_refused(scope) is True, method
    upgrade = _scope(kind="websocket", headers=[(b"origin", b"http://127.0.0.1:8000")])
    assert auth.origin_refused(upgrade) is True


def test_a_default_port_is_not_a_different_origin():
    """A tunnel host arrives portless in both Host and Origin."""
    auth = _auth()
    scope = _scope(host="desktop.example.com", headers=[(b"origin", b"https://desktop.example.com")])
    assert auth.cross_origin(scope) is False
    wrong_port = _scope(host="desktop.example.com",
                        headers=[(b"origin", b"https://desktop.example.com:8443")])
    assert auth.cross_origin(wrong_port) is True


def test_forwarded_host_counts_only_when_the_proxy_is_trusted():
    headers = [(b"origin", b"https://desktop.example.com"), (b"x-forwarded-host", b"desktop.example.com")]
    trusted = FrontDoorAuth(ServeSettings(auth="token", trust_proxy_headers=True))
    assert trusted.cross_origin(_scope(host="127.0.0.1:9129", headers=headers)) is False
    untrusted = _auth()
    assert untrusted.cross_origin(_scope(host="127.0.0.1:9129", headers=headers)) is True


def test_allowed_origins_env_is_an_escape_hatch(monkeypatch):
    monkeypatch.setenv(ALLOWED_ORIGINS_ENV, "http://127.0.0.1:8000, tauri.localhost")
    auth = _auth()
    assert auth.cross_origin(_scope(headers=[(b"origin", b"http://127.0.0.1:8000")])) is False
    assert auth.cross_origin(_scope(headers=[(b"origin", b"http://tauri.localhost")])) is False
    assert auth.cross_origin(_scope(headers=[(b"origin", b"http://127.0.0.1:8001")])) is True


def test_public_paths_are_matched_exactly():
    auth = _auth()
    for public in sorted(auth_module.PUBLIC_PATHS | {"/healthz"}):
        assert auth._is_public(public) is True, public
    for bypass in ("/login", "/mobile/anything", "/webapp/launch/anything",
                   "/webapp/window/anything", "/share/view/anything",
                   "/api/share/view/anything", "/api/mobile/pair/anything",
                   "/api/share/view/../../profiles/sessions", "/healthz/x"):
        assert auth._is_public(bypass) is False, bypass


def test_the_login_form_is_public_only_in_password_mode(tmp_path):
    password = tmp_path / "password.txt"
    password.write_text("demo-password")
    password_auth = FrontDoorAuth(ServeSettings(auth="password", password_file=password))
    assert password_auth._is_public("/login") is True
    assert _auth()._is_public("/login") is False


def test_the_login_limiter_uses_the_rightmost_forwarded_address():
    """A trusted proxy appends the peer it saw; the leftmost entry is caller text."""
    headers = [(b"x-forwarded-for", b"198.51.100.7, 10.0.0.1")]
    trusted = FrontDoorAuth(ServeSettings(trust_proxy_headers=True))
    assert trusted.client_address(_scope(headers=headers)) == "10.0.0.1"
    spoofed = [(b"x-forwarded-for", b"1.2.3.4, 198.51.100.7, 10.0.0.1")]
    assert trusted.client_address(_scope(headers=spoofed)) == "10.0.0.1"
    plain = FrontDoorAuth(ServeSettings(trust_proxy_headers=False))
    assert plain.client_address(_scope(headers=headers)) == "127.0.0.1"


def test_the_host_terminal_is_off_unless_the_bind_is_loopback():
    """The browser's host shell follows the bind address, not a wish."""
    assert ServeSettings(host="127.0.0.1").host_terminal is True
    assert ServeSettings(host="localhost").host_terminal is True
    assert ServeSettings(host="0.0.0.0").host_terminal is False
    assert ServeSettings(host="192.0.2.10").host_terminal is False
    assert ServeSettings(host="0.0.0.0", host_terminal=True).host_terminal is True
    assert host_terminal.request_allowed(ServeSettings(host="0.0.0.0")) is False
    assert host_terminal.request_allowed(ServeSettings(host="127.0.0.1")) is True
