"""Front-door authentication for the Hermes Plus sidecar."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

from starlette.responses import HTMLResponse, RedirectResponse, Response

from hermes_plus import adapter

log = logging.getLogger(__name__)

COOKIE_NAME = "hermes_plus_session"
SESSION_HEADER = "x-hermes-session-token"
# The persistent browser-session signing key, in our own data directory.
SESSION_KEY_FILE = "session.key"
SESSION_KEY_BYTES = 32
# Cookie values carry the scheme tag so a future change can tell them apart.
SESSION_COOKIE_PREFIX = "v1."
_SESSION_MAC_CONTEXT = b"hermes-plus-session-v1:"
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


def session_key_path(profile: str | None = None) -> Path:
    """Where this profile's browser-session key lives."""
    return adapter.plus_home(profile) / SESSION_KEY_FILE


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write payload to path in one step, owner-readable only (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_or_create_session_key(path: Path) -> bytes:
    """Return the persistent 32-byte session-signing key, creating it on first use.

    A file that exists but is not a key of the expected size is replaced: every
    existing browser session is invalidated, which is the same outcome as
    deleting the file by hand, and logging in keeps working. Whoever can write
    this file can already forge sessions, so self-healing costs nothing.
    """
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = b""
    except OSError as exc:
        log.warning("cannot read the session key at %s (%s)", path, exc)
        key = b""
    if len(key) == SESSION_KEY_BYTES:
        return key
    if key:
        log.warning(
            "%s is not a %d-byte key; writing a new one, which invalidates "
            "existing browser sessions", path, SESSION_KEY_BYTES,
        )
    key = secrets.token_bytes(SESSION_KEY_BYTES)
    _atomic_write(path, key)
    return key


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
        self._session_key: bytes | None = None
        self._session_key_lock = threading.Lock()

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

    def session_key(self) -> bytes:
        """This profile's persistent cookie-signing key, read once per process.

        Loading is lazy: a token-mode server (and every code path that never
        signs a browser session) never reads or creates the file.
        """
        with self._session_key_lock:
            if self._session_key is None:
                self._session_key = load_or_create_session_key(
                    session_key_path(self.settings.profile)
                )
            return self._session_key

    def _session_mac(self) -> bytes:
        """The MAC a password-mode session cookie must carry.

        The key outlives the process, so a restart does not log anyone out; the
        MAC is bound to the password digest, so changing the password (or the
        password file's contents) invalidates every cookie minted before it.
        """
        binding = self._password_digest or bytes(hashlib.sha256().digest_size)
        return hmac.new(self.session_key(), _SESSION_MAC_CONTEXT + binding, hashlib.sha256).digest()

    def session_cookie_value(self) -> str:
        """Mint the signed browser-session value used in password mode."""
        return SESSION_COOKIE_PREFIX + self._session_mac().hex()

    def valid_session_cookie(self, value: str) -> bool:
        """True when value is a session cookie this password minted and signed."""
        if self.settings.auth != "password" or not value.startswith(SESSION_COOKIE_PREFIX):
            return False
        try:
            supplied = bytes.fromhex(value[len(SESSION_COOKIE_PREFIX):])
        except ValueError:
            return False
        return hmac.compare_digest(supplied, self._session_mac())

    def authenticated(self, scope: dict[str, Any], *, websocket: bool = False) -> bool:
        if self.settings.auth == "none":
            return True
        headers = _headers(scope)
        header_token = headers.get(SESSION_HEADER.encode(), b"").decode("latin-1")
        if self.has_valid_token(header_token) or self.has_valid_token(_cookie_token(headers)):
            return True
        # Password mode signs its cookie instead of carrying the process token,
        # so one restart no longer logs every browser out.
        if self.valid_session_cookie(_cookie_token(headers)):
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
        """The Set-Cookie value for the front door.

        Token mode hands the browser the process token (unchanged). Password
        mode hands it a signed session value instead, so the token stays a
        login credential and never lands in a cookie.
        """
        value = self.session_cookie_value() if self.settings.auth == "password" else self.token
        parts = [
            f"{COOKIE_NAME}={value}",
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
