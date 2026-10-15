"""Front-door authentication for the Hermes Plus sidecar."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlencode

from starlette.responses import HTMLResponse, RedirectResponse, Response

COOKIE_NAME = "hermes_plus_session"
SESSION_HEADER = "x-hermes-session-token"
# Feature surfaces that self-secure or ride purpose tickets (the fork's
# PUBLIC_API_PATHS + public prefixes: share links, mobile pairing, webapp
# handoffs). /api/share/view is capability-token-secured, /api/mobile/pair is
# code-secured, /webapp/window-session is ticket + same-origin secured.
PUBLIC_PATHS: set[str] = {
    "/share/view",
    "/api/share/view",
    "/mobile",
    "/api/mobile/pair",
    "/webapp/window-session",
    "/webapp/launch",
    "/webapp/window",
}
# Set by the app assembly: redeem_scope(scope) admits a ticketed file URL.
def file_ticket_redeem(scope: dict[str, Any]) -> bool:
    return False


_PUBLIC_ALWAYS = frozenset({"/healthz", "/login"})
_ATTEMPTS_PER_MINUTE = 5
# Long enough for the renderer to fetch a ticket and open the socket, short
# enough that a leaked one is uninteresting. Matches upstream's TTL.
WS_TICKET_TTL_SECONDS = 30


def _headers(scope: dict[str, Any]) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in scope.get("headers", [])}


def _first_header(headers: dict[bytes, bytes], name: str) -> str:
    value = headers.get(name.encode("ascii"), b"").decode("latin-1")
    return value.split(",", 1)[0].strip()


def _cookie_token(headers: dict[bytes, bytes]) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get(b"cookie", b"").decode("latin-1"))
    except (ValueError, UnicodeError):
        return ""
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else ""


class FrontDoorAuth:
    """Own the browser session credential and enforce it at the ASGI boundary."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.token = os.environ.get("HERMES_PLUS_TOKEN") or secrets.token_urlsafe(32)
        password = os.environ.get("HERMES_PLUS_PASSWORD")
        if password is None and settings.password_file is not None:
            password = settings.password_file.read_text(encoding="utf-8").strip()
        self._password_digest = hashlib.sha256(password.encode()).digest() if password else None
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._ws_tickets: dict[str, float] = {}
        self._ws_ticket_lock = threading.Lock()

    def mint_ws_ticket(self) -> str:
        """Mint a single-use credential for a WebSocket upgrade.

        A browser cannot set an Authorization header on an upgrade, and the
        renderer must not put the session token in a URL, so it POSTs
        /api/auth/ws-ticket and dials with ``?ticket=`` instead.
        """
        ticket = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._ws_ticket_lock:
            for used, expiry in list(self._ws_tickets.items()):
                if expiry < now:
                    del self._ws_tickets[used]
            self._ws_tickets[ticket] = now + WS_TICKET_TTL_SECONDS
        return ticket

    def redeem_ws_ticket(self, ticket: str) -> bool:
        """Consume a ticket. Missing, unknown, expired and reused all fail closed."""
        if not ticket:
            return False
        with self._ws_ticket_lock:
            expiry = self._ws_tickets.pop(ticket, None)
        return expiry is not None and expiry >= time.monotonic()

    def _trusted_header(self, scope: dict[str, Any], name: str) -> str:
        if not self.settings.trust_proxy_headers:
            return ""
        return _first_header(_headers(scope), name)

    def is_https(self, scope: dict[str, Any]) -> bool:
        if scope.get("scheme") == "https":
            return True
        return self._trusted_header(scope, "x-forwarded-proto").lower() == "https"

    def client_address(self, scope: dict[str, Any]) -> str:
        forwarded = self._trusted_header(scope, "x-forwarded-for")
        if forwarded:
            return forwarded
        client = scope.get("client")
        return str(client[0]) if client else "unknown"

    def external_host(self, scope: dict[str, Any]) -> str:
        forwarded = self._trusted_header(scope, "x-forwarded-host")
        if forwarded:
            return forwarded
        return _first_header(_headers(scope), "host")

    def has_valid_token(self, candidate: str) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate, self.token)

    def authenticated(self, scope: dict[str, Any], *, websocket: bool = False) -> bool:
        if self.settings.auth == "none":
            return True
        headers = _headers(scope)
        header_token = headers.get(SESSION_HEADER.encode(), b"").decode("latin-1")
        if self.has_valid_token(header_token) or self.has_valid_token(_cookie_token(headers)):
            return True
        if not websocket and self.settings.auth == "token":
            # The fork's paired phone and its web client authenticate with a
            # bearer token (web_routers/mobile.py hands out `token` and the
            # page sends `Authorization: Bearer`); admit it in token mode.
            bearer = _first_header(headers, "authorization")
            if bearer[:7].lower() == "bearer " and self.has_valid_token(bearer[7:].strip()):
                return True
        if websocket:
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            if self.has_valid_token((query.get("token") or [""])[0]):
                return True
            return self.redeem_ws_ticket((query.get("ticket") or [""])[0])
        return False

    def consume_password_attempt(self, scope: dict[str, Any]) -> bool:
        address = self.client_address(scope)
        now = time.monotonic()
        attempts = self._attempts[address]
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(attempts) >= _ATTEMPTS_PER_MINUTE:
            return False
        attempts.append(now)
        return True

    def verify_password(self, candidate: str) -> bool:
        supplied = hashlib.sha256(candidate.encode()).digest()
        expected = self._password_digest or bytes(32)
        return hmac.compare_digest(supplied, expected) and self._password_digest is not None

    def cookie_header(self, scope: dict[str, Any]) -> bytes:
        parts = [
            f"{COOKIE_NAME}={self.token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if self.is_https(scope):
            parts.append("Secure")
        return "; ".join(parts).encode("ascii")

    async def handle(self, app: Any, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket":
            path = scope.get("path", "")
            if self._is_public(path) or self.authenticated(scope, websocket=True):
                await app(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 4401})
            return
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        path = scope.get("path", "/")
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if (
            self.settings.auth == "token"
            and path == "/"
            and scope.get("method") == "GET"
            and self.has_valid_token((query.get("token") or [""])[0])
        ):
            clean_query = [
                (key, value)
                for key, values in query.items()
                if key != "token"
                for value in values
            ]
            location = "/" + (f"?{urlencode(clean_query)}" if clean_query else "")
            response = RedirectResponse(location, status_code=303)
            response.raw_headers.append((b"set-cookie", self.cookie_header(scope)))
            await response(scope, receive, send)
            return

        # A path-bound file ticket authenticates its exact URL (single-use for
        # downloads); everything else goes through the normal gate.
        if file_ticket_redeem(scope) or self._is_public(path) or self.authenticated(scope):
            await app(scope, receive, send)
            return

        if self.settings.auth == "password" and path != "/login":
            response: Response = RedirectResponse("/login", status_code=303)
        elif path.startswith("/api/"):
            response = Response(
                json.dumps({"detail": "Unauthorized"}),
                status_code=401,
                media_type="application/json",
            )
        else:
            response = HTMLResponse("Authentication required", status_code=401)
        await response(scope, receive, send)

    @staticmethod
    def _is_public(path: str) -> bool:
        return path in _PUBLIC_ALWAYS or any(
            path == public or path.startswith(public.rstrip("/") + "/")
            for public in PUBLIC_PATHS
        )

class AuthMiddleware:
    """Pure-ASGI adapter so Starlette owns the stack and the runtime owns the auth state."""

    def __init__(self, app: Any, auth: FrontDoorAuth) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self.auth.handle(self.app, scope, receive, send)


async def login_page(request: Any) -> Response:
    """Render the password form and establish the browser session on success."""
    auth: FrontDoorAuth = request.app.state.auth
    if request.method == "GET":
        return HTMLResponse(
            "<!doctype html><html><meta charset=utf-8><title>Hermes Plus login</title>"
            "<form method=post><label>Password <input name=password type=password "
            "autocomplete=current-password required></label><button>Sign in</button></form></html>"
        )
    if not auth.consume_password_attempt(request.scope):
        return Response("Too many login attempts", status_code=429)
    body = (await request.body()).decode("utf-8", errors="replace")
    password = (parse_qs(body).get("password") or [""])[0]
    if not auth.verify_password(password):
        return HTMLResponse("Invalid password", status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.raw_headers.append((b"set-cookie", auth.cookie_header(request.scope)))
    return response
