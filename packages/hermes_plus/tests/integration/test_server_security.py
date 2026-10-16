"""Pre-publish security checks against a real sidecar (and, for the route table,
the real app object built in-process).

Each test reproduces a finding from the S40 audit: every one of them fails
against the pre-fix behaviour. No mocks: a real `hermes-plus serve` over real
stock Hermes in a throwaway HERMES_HOME, driven over real HTTP and WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets
from httpx import ASGITransport, AsyncClient

from conftest import HERMES_BIN, Sidecar, make_sidecar
from hermes_plus.server.app import build_app
from hermes_plus.server.auth import PUBLIC_PATHS
from hermes_plus.server.runtime import Runtime
from hermes_plus.settings import ServeSettings

pytestmark = pytest.mark.integration

COOKIE = "hermes_plus_session"

#: The only paths (and methods) that may answer without a credential, with the
#: status each is expected to produce for an unauthenticated caller. Everything
#: else must be 401. A new route fails this test until it is classified here.
PUBLIC_EXPECTATIONS: dict[tuple[str, str], int] = {
    ("/healthz", "GET"): 200,
    ("/healthz", "HEAD"): 200,
    ("/api/plus/bridge/rpc", "POST"): 401,   # loopback-only + its own token
    ("/api/share/view", "GET"): 404,         # capability token in the query
    ("/api/share/view", "HEAD"): 404,
    ("/share/view", "GET"): 404,
    ("/share/view", "HEAD"): 404,
    ("/api/mobile/pair", "POST"): 403,       # pairing code in the body
    ("/mobile", "GET"): 200,
    ("/mobile", "HEAD"): 200,
    ("/webapp/window-session", "POST"): 403, # ticket + same-origin only
    ("/webapp/launch", "GET"): 200,
    ("/webapp/launch", "HEAD"): 200,
    ("/webapp/window", "GET"): 200,
    ("/webapp/window", "HEAD"): 200,
}

#: Path templates that only exist as catch-alls, probed with a concrete URL.
PROBE_URLS = {"/{path:path}": "/spa-probe", "/api/{path:path}": "/api/spa-probe"}


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _cookie(sidecar: Sidecar) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={sidecar.token}"}


def _ui_dir(root: Path) -> Path:
    ui = root / "ui"
    (ui / "assets").mkdir(parents=True, exist_ok=True)
    (ui / "index.html").write_text(
        "<!doctype html><html><head><title>demo</title></head><body>demo-ui</body></html>",
        encoding="utf-8",
    )
    return ui


@pytest.fixture(scope="module")
def password_sidecar(tmp_path_factory: pytest.TempPathFactory) -> Sidecar:
    """Password mode with a trusted proxy in front, as docker/README.md prescribes."""
    if not HERMES_BIN.exists():
        pytest.skip(f"stock Hermes binary not available at {HERMES_BIN}")
    server = make_sidecar(
        tmp_path_factory.mktemp("hermes-home-password"),
        env_extra={
            "HERMES_PLUS_AUTH": "password",
            "HERMES_PLUS_PASSWORD": "demo-audit-password",
            "HERMES_PLUS_TRUST_PROXY_HEADERS": "1",
        },
    )
    yield server
    server.stop()


def _app(settings: ServeSettings, runtime: Runtime) -> Any:
    return build_app(settings, runtime)


async def test_every_route_requires_auth_except_the_public_set(tmp_path: Path, monkeypatch):
    """Walk the mounted routes of the real app object and hit each one bare."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_PLUS_TOKEN", "route-table-token")
    settings = ServeSettings(auth="token", ui_dir=_ui_dir(tmp_path))
    app = _app(settings, Runtime(settings))

    table: list[tuple[str, str, int | None]] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:9129") as client:
        for route in app.routes:
            if type(route).__name__ == "WebSocketRoute":
                assert route.path not in PUBLIC_PATHS, f"WebSocket {route.path} is public"
                table.append((route.path, "WS", None))
                continue
            for method in sorted(getattr(route, "methods", None) or ["GET"]):
                url = PROBE_URLS.get(route.path, route.path)
                response = await client.request(method, url)
                expected = PUBLIC_EXPECTATIONS.get((route.path, method), 401)
                table.append((route.path, method, response.status_code))
                assert response.status_code == expected, (route.path, method, response.status_code)
                if expected == 401:
                    assert "route-table-token" not in response.text, route.path

    # Every HTTP route was walked, and the public set is exactly what answered.
    assert {path for path, _, _ in table} >= {
        "/healthz", "/api/plus/info", "/api/ws", "/api/auth/ws-ticket", "/api/files/download"}
    answered = len([row for row in table if row[2] not in (401, None)])
    assert answered == len([status for status in PUBLIC_EXPECTATIONS.values() if status != 401])


async def test_no_public_sub_path_hands_out_the_session_token(tmp_path: Path, monkeypatch):
    """The prefix rule made /login, /mobile/x and /webapp/launch/x serve the SPA
    document with the live session token in it, to an unauthenticated caller."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_PLUS_TOKEN", "leak-canary-token")
    settings = ServeSettings(auth="token", ui_dir=_ui_dir(tmp_path))
    app = _app(settings, Runtime(settings))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:9129") as client:
        for url in ("/login", "/mobile/anything", "/webapp/launch/anything",
                    "/webapp/window/anything", "/share/view/anything",
                    "/api/share/view/anything", "/healthz/anything"):
            response = await client.get(url)
            assert response.status_code == 401, url
            assert "leak-canary-token" not in response.text, url


def test_public_sub_paths_cannot_reach_the_api_proxy(sidecar: Sidecar):
    """``/api/share/view/../../profiles/sessions`` was proxied to stock Hermes
    with the internal upstream token: the whole REST surface, unauthenticated."""
    traversal = "/api/share/view/..%2f..%2fprofiles/sessions"
    anonymous = httpx.get(f"{sidecar.url}{traversal}", timeout=30)
    assert anonymous.status_code == 401, anonymous.text
    assert "sessions" not in anonymous.text

    # Even authenticated, the dot segment is refused rather than normalised away.
    authed = httpx.get(f"{sidecar.url}{traversal}", headers=_auth(sidecar), timeout=30)
    assert authed.status_code == 400, authed.text


def test_the_login_route_never_serves_the_token(sidecar: Sidecar, password_sidecar: Sidecar):
    anonymous = httpx.get(f"{sidecar.url}/login", timeout=30)
    assert anonymous.status_code == 401
    assert sidecar.token not in anonymous.text

    form = httpx.get(f"{password_sidecar.url}/login", timeout=30)
    assert form.status_code == 200
    assert "<form" in form.text
    assert password_sidecar.token not in form.text


def test_cross_origin_state_changing_requests_are_refused(sidecar: Sidecar):
    uploads = sidecar.home / "uploads"
    before = sorted(p.name for p in uploads.iterdir()) if uploads.is_dir() else []
    evil = {"Origin": "http://127.0.0.1:8000", **_cookie(sidecar)}

    refused = httpx.post(
        f"{sidecar.url}/api/chat/file-upload",
        files={"file": ("demo.txt", b"demo")},
        headers=evil,
        timeout=30,
    )
    assert refused.status_code == 403, refused.text
    after = sorted(p.name for p in uploads.iterdir()) if uploads.is_dir() else []
    assert after == before, "a cross-origin upload was staged"

    for path, body in [("/api/share/create", {"session_id": "demo"}),
                       ("/api/auth/ws-ticket", None),
                       ("/api/git/worktree/ensure", {"path": "/tmp"})]:
        reply = httpx.post(f"{sidecar.url}{path}", json=body, headers=evil, timeout=30)
        assert reply.status_code == 403, (path, reply.status_code)

    null_origin = httpx.post(f"{sidecar.url}/api/auth/ws-ticket",
                             headers={"Origin": "null", **_cookie(sidecar)}, timeout=30)
    assert null_origin.status_code == 403

    # Same-origin browsers, and non-browser clients that send no Origin at all,
    # keep working: with the cookie, with the header token, and with neither.
    same_origin = httpx.post(
        f"{sidecar.url}/api/auth/ws-ticket",
        headers={"Origin": f"http://127.0.0.1:{sidecar.port}", **_cookie(sidecar)},
        timeout=30,
    )
    assert same_origin.status_code == 200, same_origin.text
    no_origin = httpx.post(f"{sidecar.url}/api/auth/ws-ticket", headers=_cookie(sidecar), timeout=30)
    assert no_origin.status_code == 200
    header_only = httpx.post(f"{sidecar.url}/api/auth/ws-ticket", headers=_auth(sidecar), timeout=30)
    assert header_only.status_code == 200


async def _ws_status(sidecar: Sidecar, path: str, headers: dict[str, str], query: str = "") -> Any:
    url = f"ws://127.0.0.1:{sidecar.port}{path}{query}"
    try:
        async with websockets.connect(url, additional_headers=headers, open_timeout=30) as ws:
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
    except websockets.exceptions.InvalidStatus as exc:
        return exc.response.status_code
    except websockets.exceptions.ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd else None


def test_cross_origin_websockets_are_refused(sidecar: Sidecar):
    """Lax cookies ride on same-site upgrades, and proxy.py rewrites Origin
    upstream, so a page on another localhost port could drive these sockets."""
    evil = {"Origin": "http://127.0.0.1:8000", **_cookie(sidecar)}
    for path in ("/api/ws", "/api/host-terminal", "/api/pty"):
        assert asyncio.run(_ws_status(sidecar, path, evil)) == 403, path

    ready = asyncio.run(_ws_status(
        sidecar, "/api/ws", {"Origin": f"http://127.0.0.1:{sidecar.port}", **_cookie(sidecar)}))
    assert ready["params"]["type"] == "gateway.ready"
    no_origin = asyncio.run(_ws_status(sidecar, "/api/ws", _cookie(sidecar)))
    assert no_origin["params"]["type"] == "gateway.ready"

    # A ticket is not a way around it either: the Origin check runs first.
    ticket = httpx.post(f"{sidecar.url}/api/auth/ws-ticket", headers=_auth(sidecar), timeout=30)
    assert ticket.status_code == 200
    assert asyncio.run(_ws_status(sidecar, "/api/ws", {"Origin": "http://evil.example.com"},
                                  f"?ticket={ticket.json()['ticket']}")) == 403


def _log_lines(sidecar: Sidecar, *, until: str, timeout: float = 30.0) -> list[str]:
    """The sidecar's log, once it holds ``until`` (the WS teardown is async)."""
    deadline = time.monotonic() + timeout
    while True:
        lines = sidecar.log_path.read_text(errors="replace").splitlines()
        if until in "\n".join(lines) or time.monotonic() > deadline:
            return lines
        time.sleep(0.25)


def test_the_access_log_records_no_capability(sidecar: Sidecar):
    """The printed one-time login URL is ``/?token=…``: uvicorn logged it verbatim."""
    login = httpx.get(f"{sidecar.url}/", params={"token": sidecar.token},
                      follow_redirects=False, timeout=30)
    assert login.status_code == 303

    managed = sidecar.home / "files" / "demo.bin"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(b"demo")
    minted = httpx.post(f"{sidecar.url}/api/files/ticket", json={"route": "download", "path": str(managed)},
                        headers=_auth(sidecar), timeout=30).json()["ticket"]
    download = httpx.get(f"{sidecar.url}/api/files/download",
                         params={"path": str(managed), "ticket": minted}, timeout=30)
    assert download.status_code == 200

    share_token = secrets.token_urlsafe(32)
    httpx.get(f"{sidecar.url}/api/share/view", params={"t": share_token}, timeout=30)
    ws_ticket = httpx.post(f"{sidecar.url}/api/auth/ws-ticket", headers=_auth(sidecar),
                           timeout=30).json()["ticket"]
    assert asyncio.run(_ws_status(sidecar, "/api/ws", {}, f"?ticket={ws_ticket}"))["params"]["type"] \
        == "gateway.ready"

    lines = _log_lines(sidecar, until='WS /api/ws?ticket=REDACTED" 101')
    access_lines = [line for line in lines if ' - "' in line]
    assert access_lines, "the access log recorded nothing"
    for secret in (sidecar.token, minted, share_token, ws_ticket):
        for line in access_lines:
            assert secret not in line, line
    # The token appears only in the startup banner, which is how the operator
    # gets the login URL in the first place.
    for line in lines:
        if sidecar.token in line:
            assert "Hermes Plus login" in line, line
    assert any("token=REDACTED" in line for line in access_lines)
    # The handshake still shows up, with its ticket redacted.
    assert any('WS /api/ws?ticket=REDACTED" 101' in line for line in access_lines), access_lines[-5:]


def test_security_headers_and_file_serving(sidecar: Sidecar, password_sidecar: Sidecar):
    page = httpx.get(f"{sidecar.url}/", headers=_auth(sidecar), timeout=30)
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"

    form = httpx.get(f"{password_sidecar.url}/login", timeout=30)
    assert form.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in form.headers["content-security-policy"]

    files = sidecar.home / "files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "demo.html").write_text("<script>alert(document.cookie)</script>", encoding="utf-8")
    for name in ("demo.html", "demo.bin"):
        target = files / name
        target.write_bytes(b"<script>alert(1)</script>")
        ticket = httpx.post(f"{sidecar.url}/api/files/ticket",
                            json={"route": "download", "path": str(target)},
                            headers=_auth(sidecar), timeout=30).json()["ticket"]
        reply = httpx.get(f"{sidecar.url}/api/files/download",
                          params={"path": str(target), "ticket": ticket}, timeout=30)
        assert reply.status_code == 200, reply.text
        assert reply.headers["x-content-type-options"] == "nosniff"
        disposition = reply.headers.get("content-disposition", "")
        assert disposition.startswith("attachment"), (name, disposition)


def test_static_serving_refuses_traversal_and_symlinks(sidecar: Sidecar):
    outside = sidecar.home / "outside-demo.txt"
    outside.write_text("outside-demo-secret", encoding="utf-8")
    link = sidecar.ui_dir / "escape.html"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(outside)

    escape = httpx.get(f"{sidecar.url}/escape.html", headers=_auth(sidecar), timeout=30)
    assert escape.status_code == 404, escape.text
    assert "outside-demo-secret" not in escape.text

    for path in ("/..%2foutside-demo.txt", "/%2e%2e/outside-demo.txt",
                 "/..%2f..%2foutside-demo.txt", "/a%00b.txt"):
        reply = httpx.get(f"{sidecar.url}{path}", headers=_auth(sidecar), timeout=30)
        assert reply.status_code == 404, (path, reply.status_code)
        assert "outside-demo-secret" not in reply.text, path


def test_a_bad_mobile_pair_body_is_a_refusal_not_a_500(sidecar: Sidecar):
    for body in (b"", b"not json"):
        reply = httpx.post(f"{sidecar.url}/api/mobile/pair", content=body,
                           headers={"Content-Type": "application/json"}, timeout=30)
        assert reply.status_code == 403, reply.text
        assert reply.json()["detail"] == "Invalid or expired pairing code"


def test_the_login_limit_survives_a_forged_forwarded_for(password_sidecar: Sidecar):
    """Behind a trusted proxy the limiter counted the caller's own leftmost
    X-Forwarded-For entry, so every attempt could pick a fresh bucket."""
    def attempt(address: str) -> int:
        reply = httpx.post(
            f"{password_sidecar.url}/login",
            data={"password": "wrong-password"},
            headers={"X-Forwarded-For": f"{address}, 10.0.0.1"},
            timeout=30,
        )
        return reply.status_code

    statuses = [attempt(f"198.51.100.{index}") for index in range(1, 8)]
    assert statuses[:5] == [401] * 5, statuses
    assert statuses[5:] == [429, 429], statuses
