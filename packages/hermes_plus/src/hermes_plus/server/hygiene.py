"""Cross-cutting request and response hygiene: a redacted access log, and the
headers a browser needs to behave.

Uvicorn's built-in access log prints the whole request target, so
``GET /?token=<session token>`` — the one-time login URL the CLI prints —
landed in the log file with the live token in it (and the share, file and
WebSocket capability tokens with it). The middleware here replaces that log
line: same shape, query string redacted. The security headers stop a served
file from being re-typed by the browser and stop an attacker page from framing
the chat.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Our own channel, not ``uvicorn.access``: with ``access_log=False`` uvicorn
#: leaves that logger handler-less and non-propagating, so anything logged
#: there is silently dropped.
log = logging.getLogger("hermes_plus.access")

# Query parameters that carry a capability: their values never reach the log,
# whatever a route happens to call them.
SECRET_QUERY_KEYS = frozenset(
    {"token", "ticket", "t", "password", "code", "access_token", "api_key", "key"}
)
_REDACTED = "REDACTED"


def redact_query(raw: bytes, *, token: str = "") -> str:
    """The query string with capability values replaced, for logging."""
    items = parse_qsl(raw.decode("latin-1"), keep_blank_values=True)
    if not items:
        return ""
    return urlencode([
        (
            key,
            _REDACTED
            if key.lower() in SECRET_QUERY_KEYS or (token and value == token)
            else value,
        )
        for key, value in items
    ])


class AccessLogMiddleware:
    """One line per request: method, target with the query redacted, status."""

    def __init__(self, app: ASGIApp, *, token: str = "") -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        outcome: Any = None

        async def observe(message: Message) -> None:
            nonlocal outcome
            if outcome is None:
                if message["type"] == "http.response.start":
                    outcome = message["status"]
                elif message["type"] == "websocket.accept":
                    outcome = 101
                elif message["type"] == "websocket.close":
                    # Refused before the handshake completed: uvicorn answers 403.
                    outcome = 403
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            if outcome is not None:
                log.info('%s - "%s %s%s" %s', self._peer(scope), self._method(scope),
                         self._target(scope), self._query(scope), outcome)

    @staticmethod
    def _peer(scope: Scope) -> str:
        client = scope.get("client")
        return f"{client[0]}:{client[1]}" if client else "unknown"

    @staticmethod
    def _method(scope: Scope) -> str:
        return "WS" if scope["type"] == "websocket" else scope.get("method", "?")

    @staticmethod
    def _target(scope: Scope) -> str:
        return scope.get("path", "/")

    def _query(self, scope: Scope) -> str:
        query = redact_query(scope.get("query_string", b""), token=self.token)
        return f"?{query}" if query else ""


class WebSocketHandshakeFilter(logging.Filter):
    """Drop uvicorn's own WebSocket handshake line, which prints the whole target.

    Uvicorn logs ``127.0.0.1:1 - "WebSocket /api/ws?ticket=…" [accepted]`` on the
    ``uvicorn.error`` logger whatever ``access_log`` is set to, so a WS ticket
    (or a ``?token=``) landed in the log even with the access log off.
    ``AccessLogMiddleware`` records the same handshake with the query redacted,
    so nothing is lost.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a record we cannot render is not ours to drop
            return True
        return '"WebSocket ' not in message


def install_log_filters() -> None:
    """Make the process's logging stop printing capability-bearing URLs.

    Called by ``server.app.run`` before uvicorn starts. Filters survive
    uvicorn's own ``dictConfig`` pass (it replaces handlers and levels, not the
    logger's filter list), and the filter is idempotent.
    """
    target = logging.getLogger("uvicorn.error")
    if not any(isinstance(existing, WebSocketHandshakeFilter) for existing in target.filters):
        target.addFilter(WebSocketHandshakeFilter())


class SecurityHeadersMiddleware:
    """Give every response the headers a browser needs to behave.

    ``nosniff`` stops the browser re-typing a served file, so an uploaded
    ``.html`` cannot be executed as the sidecar's origin even if a future route
    forgets ``Content-Disposition: attachment``. ``DENY`` plus
    ``frame-ancestors 'none'`` stop an attacker page framing the chat and
    harvesting clicks through it (approving a tool call, dropping a file on the
    composer). A document that sets its own policy (the webapp handoff pages)
    keeps it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def decorate(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-content-type-options", "nosniff")
                if headers.get("content-type", "").startswith(("text/html", "application/xhtml+xml")):
                    headers.setdefault("x-frame-options", "DENY")
                    headers.setdefault("content-security-policy", "frame-ancestors 'none'")
            await send(message)

        await self.app(scope, receive, decorate)
