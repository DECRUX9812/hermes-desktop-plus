"""Webapp-only routes: the one-use handoffs (child window, auto-opened launch
tab) and the host terminal — a port of fork web_routers/webapp.py.

A one-use ticket exchanged at the public, same-origin ``/webapp/window-session``
is the only way a credential-free window gets the sidecar session token; the
exchange is body-free and the ticket rides the ``X-Hermes-Window-Ticket``
header. Launch tickets additionally require the redeeming connection to belong
to the OS user running the server (Linux /proc/net/tcp uid match).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import sys
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute

from hermes_plus.http import host_terminal

log = logging.getLogger(__name__)

WINDOW_TICKET_TTL_SECONDS = 30
#: A sandboxed (snap/Flatpak) browser's cold start can take several seconds to reach the page.
LAUNCH_TICKET_TTL_SECONDS = 60
_MAX_WINDOW_TICKETS = 128
_ticket_lock = threading.Lock()
_window_tickets: dict[str, tuple[float, str, bool]] = {}  # ticket -> (expire_monotonic, token, is_launch)


def mint_launch_ticket(token: str) -> str:
    """Ticket for the browser the server opens itself: a launcher's argv carries this, not the token."""
    return _mint_handoff_ticket(token, LAUNCH_TICKET_TTL_SECONDS, launch=True)


def _mint_handoff_ticket(token: str, ttl: float, *, launch: bool) -> str:
    now = time.monotonic()
    with _ticket_lock:
        tickets = {key: value for key, value in _window_tickets.items()
                   if value[0] > now and value[1] == token}
        if len(tickets) >= _MAX_WINDOW_TICKETS:
            raise _TooManyTickets()
        ticket = secrets.token_urlsafe(32)
        tickets[ticket] = (now + ttl, token, launch)
        _window_tickets.clear()
        _window_tickets.update(tickets)
    return ticket


class _TooManyTickets(Exception):
    pass


def _http_origin(raw: str) -> str | None:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(raw or "")
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    except ValueError:
        return None


def _loopback_peer_uid(client: tuple[str, int], server: tuple[str, int]) -> int | None:
    """Owner UID of the client end of a local TCP connection, from Linux ``/proc/net/tcp*``.

    Both ends of a loopback connection are listed with their socket owner; the client's is
    the row whose local address is *client* and remote address is *server*. None when absent
    (another network namespace, a closed connection) or unreadable.
    """
    def _normal(host: str | bytes, port: int):
        address = ipaddress.ip_address(host)
        return getattr(address, "ipv4_mapped", None) or address, port

    def _row_address(field: str):
        host_hex, port_hex = field.split(":")
        raw = bytes.fromhex(host_hex)
        # The kernel prints each 32-bit word of the address in host byte order.
        packed = b"".join(int.from_bytes(raw[i:i + 4], "big").to_bytes(4, sys.byteorder)
                          for i in range(0, len(raw), 4))
        return _normal(packed, int(port_hex, 16))

    try:
        wanted = (_normal(*client), _normal(*server))
    except ValueError:
        return None
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, encoding="ascii") as stream:
                rows = stream.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            try:
                if (_row_address(fields[1]), _row_address(fields[2])) == wanted:
                    return int(fields[7])
            except (IndexError, ValueError):
                continue
    return None


def _peer_is_server_user(request: Request) -> bool:
    """Whether this request's connection belongs to the OS user running the server.

    Linux authenticates the connection owner. macOS has no authenticated
    launch handoff here, so an argv-carried launch ticket cannot be redeemed.
    Authenticated child-window tickets do not use this launcher channel.
    """
    if sys.platform == "darwin":
        return False
    if sys.platform != "linux":
        return True
    client, server = request.client, request.scope.get("server")
    if client is None or not server:
        return False
    return _loopback_peer_uid((client.host, client.port), (server[0], server[1])) == os.getuid()


async def issue_window_ticket(request: Request) -> JSONResponse:
    auth = request.app.state.auth
    try:
        ticket = _mint_handoff_ticket(auth.token, WINDOW_TICKET_TTL_SECONDS, launch=False)
    except _TooManyTickets:
        return JSONResponse(
            {"detail": "Too many pending Webapp windows; try again shortly"}, status_code=429)
    return JSONResponse({"ticket": ticket}, headers={"Cache-Control": "no-store"})


async def redeem_window_ticket(request: Request) -> JSONResponse:
    auth = request.app.state.auth
    # This endpoint deliberately sits outside /api: the child window or launch
    # tab has no session yet. Only a purpose-specific ticket can exchange for
    # the existing one.
    origins = request.headers.getlist("origin") if hasattr(request.headers, "getlist") else []
    origin = _http_origin(origins[0]) if len(origins) == 1 else None
    target_origin = _http_origin(f"{request.url.scheme}://{request.url.netloc}")
    if origin is None or origin != target_origin:
        return JSONResponse(
            {"detail": "Webapp window handoff requires a same-origin request"}, status_code=403)
    # Keep the unauthenticated exchange body-free; never parse an arbitrary
    # JSON/multipart payload just to extract a fixed-size capability.
    ticket = request.headers.get("X-Hermes-Window-Ticket", "")
    with _ticket_lock:
        entry = _window_tickets.get(ticket)
        # A launch ticket crossed a browser launcher's argv: only a connection owned by this
        # server's user may spend it, and a refused attempt must not burn it for the real browser.
        if entry is not None and entry[2] and not _peer_is_server_user(request):
            entry = None
        elif entry is not None:
            del _window_tickets[ticket]
    if entry is None or entry[0] <= time.monotonic() or entry[1] != auth.token:
        return JSONResponse(
            {"detail": "Webapp window link expired or was already used; open a new window"},
            status_code=403)
    return JSONResponse({"token": auth.token}, headers={"Cache-Control": "no-store"})


_LAUNCH_SCRIPT = r"""
const basePath = __BASE_PATH__;
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const ticket = params.get('ticket') || '';
const fail = () => {
  document.getElementById('status').textContent = 'This launch link was already used or has expired. '
    + 'Open the private link printed by hermes-plus serve, or restart it for a new one.';
};
if (!/^[A-Za-z0-9_-]{43}$/.test(ticket)) {
  fail();
} else {
  fetch(`${basePath}/webapp/window-session`, {
    method: 'POST', credentials: 'same-origin', headers: {'X-Hermes-Window-Ticket': ticket}
  }).then(response => response.ok ? response.json() : Promise.reject()).then(({token}) => {
    if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error();
    // The SPA consumes and strips this fragment exactly as it does for the printed link.
    const target = new URL(`${basePath}/`, location.origin);
    target.search = params.get('query') || '';
    target.hash = `hermes-session=${token}`;
    location.replace(target.href);
  }).catch(fail);
}
"""


def _handoff_page(request: Request, title: str, status: str, script: str) -> HTMLResponse:
    """Credential-free, uncached page whose only script is *script* (``__BASE_PATH__`` bound)."""
    prefix = request.app.state.runtime.settings.base_path.rstrip("/")
    nonce = secrets.token_urlsafe(16)
    script = script.replace("__BASE_PATH__", json.dumps(prefix).replace("</", "<\\/"))
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
        f'<title>{title}</title><p id="status">{status}</p>'
        f'<script nonce="{nonce}">{script}</script>',
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        },
    )


async def launch_page(request: Request) -> HTMLResponse:
    """Where the server's own browser-open lands: spends the one-use launch ticket from the
    fragment (never sent to the server or kept in history) and enters the Webapp."""
    return _handoff_page(request, "Opening Hermes", "Opening Hermes Webapp…", _LAUNCH_SCRIPT)


# No credential appears in this public page or in its URL. The fragment
# names a transient channel; the authorized parent mints a one-use ticket.
_WINDOW_SCRIPT = r"""
const basePath = __BASE_PATH__;
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const id = params.get('channel') || '';
const status = document.getElementById('status');
if (!/^[a-f0-9-]{36}$/.test(id)) {
  status.textContent = 'Open this window from an authorized Hermes Webapp tab.';
} else {
  const channel = new BroadcastChannel(`hermes.webapp.window:${id}`);
  let receiving = false;
  let finished = false;
  const controller = new AbortController();
  const fail = message => {
    if (finished) return;
    finished = true;
    controller.abort();
    status.textContent = message;
    channel.postMessage({type: 'error'});
    channel.close();
    clearTimeout(timeout);
  };
  const timeout = setTimeout(() => fail('This window link expired. Open a new window from the original tab.'), 30000);
  channel.onmessage = async event => {
    if (receiving) return;
    if (event.data?.error) { fail(event.data.error); return; }
    const ticket = event.data?.ticket;
    if (typeof ticket !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(ticket)) return;
    receiving = true;
    try {
      const response = await fetch(`${basePath}/webapp/window-session`, {
        method: 'POST', credentials: 'same-origin',
        headers: {'X-Hermes-Window-Ticket': ticket}, signal: controller.signal
      });
      if (!response.ok) throw new Error('Window authorization expired. Open a new window from the original tab.');
      const {token} = await response.json();
      if (finished) return;
      if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('Invalid window authorization.');
      sessionStorage.setItem(`hermes.webapp.session.v1:${JSON.stringify([location.origin, basePath])}`, token);
      const target = new URL(`${basePath}/`, location.origin);
      target.search = params.get('query') || '';
      target.hash = params.get('route') || '/';
      channel.postMessage({type: 'done'});
      finished = true;
      clearTimeout(timeout);
      channel.close();
      location.replace(target.href);
    } catch (error) { fail(error.message || 'Could not authorize this window. Open it again from the original tab.'); }
  };
  channel.postMessage({type: 'ready'});
}
"""


async def child_window_page(request: Request) -> HTMLResponse:
    return _handoff_page(request, "Opening Hermes", "Authorizing this Hermes window…", _WINDOW_SCRIPT)


async def host_terminal_ws(websocket: Any) -> None:
    await host_terminal.handle_host_terminal_ws(websocket)


routes = [
    Route("/api/webapp/window-ticket", issue_window_ticket, methods=["POST"]),
    Route("/webapp/window-session", redeem_window_ticket, methods=["POST"]),
    Route("/webapp/launch", launch_page, methods=["GET"]),
    Route("/webapp/window", child_window_page, methods=["GET"]),
    WebSocketRoute("/api/host-terminal", host_terminal_ws),
]
