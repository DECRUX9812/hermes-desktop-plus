"""Access-log redaction and security headers (units plus one real ASGI pass)."""

from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from hermes_plus.server.hygiene import (
    AccessLogMiddleware,
    SecurityHeadersMiddleware,
    WebSocketHandshakeFilter,
    redact_query,
)


def test_redact_query_hides_capabilities():
    assert redact_query(b"token=abc123") == "token=REDACTED"
    assert redact_query(b"t=share-token&after_id=4") == "t=REDACTED&after_id=4"
    assert redact_query(b"path=%2Ftmp%2Fdemo.bin&ticket=tkt") == "path=%2Ftmp%2Fdemo.bin&ticket=REDACTED"
    assert redact_query(b"") == ""


def test_redact_query_hides_the_session_token_under_any_name():
    """A future route must not be able to leak the token by renaming the parameter."""
    assert redact_query(b"session=live-token", token="live-token") == "session=REDACTED"
    assert redact_query(b"session=other", token="live-token") == "session=other"


def _app() -> Starlette:
    async def html(request):
        return HTMLResponse("<html><body>demo</body></html>")

    async def json(request):
        return JSONResponse({"ok": True})

    async def own_policy(request):
        return HTMLResponse("<html><body>demo</body></html>",
                            headers={"Content-Security-Policy": "default-src 'none'"})

    app = Starlette(routes=[Route("/html", html), Route("/json", json), Route("/own", own_policy)])
    return SecurityHeadersMiddleware(app)


async def test_security_headers_on_html_and_json():
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        html = await client.get("/html")
        assert html.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in html.headers["content-security-policy"]
        assert html.headers["x-content-type-options"] == "nosniff"

        plain = await client.get("/json")
        assert "x-frame-options" not in plain.headers
        assert plain.headers["x-content-type-options"] == "nosniff"

        own = await client.get("/own")
        assert own.headers["content-security-policy"] == "default-src 'none'"
        assert own.headers["x-frame-options"] == "DENY"


def test_uvicorn_websocket_handshake_lines_are_dropped():
    """Uvicorn logs the WS target on uvicorn.error whatever access_log says."""
    filter_ = WebSocketHandshakeFilter()
    for template, status in (('%s - "WebSocket %s" [accepted]', "[accepted]"),
                             ('%s - "WebSocket %s" 403', "403")):
        record = logging.LogRecord(
            "uvicorn.error", logging.INFO, __file__, 1, template,
            ("127.0.0.1:5000", "/api/ws?ticket=demo-ticket"), None,
        )
        assert status in record.getMessage()
        assert filter_.filter(record) is False

    kept = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                             "Application startup complete.", None, None)
    assert filter_.filter(kept) is True


async def test_access_log_line_never_carries_the_query_token(caplog):
    from httpx import ASGITransport, AsyncClient

    async def ok(request):
        return JSONResponse({"ok": True})

    app = AccessLogMiddleware(Starlette(routes=[Route("/", ok)]), token="live-token")
    transport = ASGITransport(app=app)
    with caplog.at_level("INFO", logger="hermes_plus.access"):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.get("/?token=live-token&profile=demo")
    line = next(record.getMessage() for record in caplog.records if "GET /" in record.getMessage())
    assert "live-token" not in line
    assert "token=REDACTED" in line
    assert "profile=demo" in line
