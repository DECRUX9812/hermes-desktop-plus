"""Webapp-only routes: window ticketing and host terminal."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from hermes_plus.http import host_terminal

log = logging.getLogger(__name__)

WINDOW_TICKET_TTL_SECONDS = 30
LAUNCH_TICKET_TTL_SECONDS = 60
_MAX_WINDOW_TICKETS = 128
_ticket_lock = threading.Lock()
_window_tickets: dict[str, tuple[float, str, bool]] = {}  # ticket -> (expire_time, token, is_launch)


async def issue_window_ticket(request: Request) -> Any:
    """Mint a one-use ticket for child window authorization."""
    runtime = request.app.state.runtime
    auth = request.app.state.auth

    # Require auth token in header or cookie
    if not auth.authenticated(request.scope):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    now = time.monotonic()
    with _ticket_lock:
        # Clean expired tickets
        tickets = {
            k: v
            for k, v in _window_tickets.items()
            if v[0] > now and v[1] == auth.token
        }
        _window_tickets.clear()
        _window_tickets.update(tickets)

        if len(_window_tickets) >= _MAX_WINDOW_TICKETS:
            return JSONResponse(
                {"detail": "Too many pending windows; try again shortly"},
                status_code=429,
            )

        ticket = secrets.token_urlsafe(32)
        _window_tickets[ticket] = (now + WINDOW_TICKET_TTL_SECONDS, auth.token, False)

    return JSONResponse(
        {"ticket": ticket},
        headers={"Cache-Control": "no-store"},
    )


async def redeem_window_ticket(request: Request) -> Any:
    """Exchange a one-use ticket for a session token."""
    auth = request.app.state.auth

    # This endpoint is intentionally outside /api: the child window hasn't
    # authenticated yet. Only a valid ticket can exchange for the auth token.
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    ticket = (body.get("ticket") or "").strip()
    now = time.monotonic()

    with _ticket_lock:
        entry = _window_tickets.pop(ticket, None)

    if entry is None or entry[0] <= now:
        return JSONResponse(
            {"detail": "Ticket expired or invalid"},
            status_code=403,
        )

    # Return the auth token so the child window can authenticate
    return JSONResponse(
        {"token": entry[1]},
        headers={"Cache-Control": "no-store"},
    )


async def launch_page(request: Request) -> Any:
    """Handoff page for the launcher: redeems a launch ticket from argv."""
    auth = request.app.state.auth

    # This is a public page (not auth-gated), but it can only access tickets
    # minted by the server itself. The launch ticket is passed in the URL fragment.
    nonce = secrets.token_urlsafe(16)
    base_path = request.app.state.runtime.settings.base_path.rstrip("/")

    script = f"""
const basePath = '{json.dumps(base_path)}';
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const ticket = params.get('ticket') || '';
const fail = () => {{
  document.getElementById('status').textContent = 
    'This launch link was already used or has expired. Open the private link printed by hermes-plus serve.';
}};
if (!/^[A-Za-z0-9_-]{{43}}$/.test(ticket)) {{
  fail();
}} else {{
  fetch(basePath + '/webapp/window-session', {{
    method: 'POST',
    credentials: 'same-origin',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ticket: ticket}})
  }}).then(response => response.ok ? response.json() : Promise.reject()).then(({{"token": token}}) => {{
    if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{{43}}$/.test(token)) throw new Error();
    const target = new URL(basePath + '/', location.origin);
    target.hash = 'hermes-session=' + token;
    location.replace(target.href);
  }}).catch(fail);
}}
"""

    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<title>Opening Hermes</title><p id="status">Opening Hermes…</p>
<script nonce="{nonce}">{script}</script>""",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        },
    )


async def child_window_page(request: Request) -> Any:
    """Handoff page for child windows."""
    auth = request.app.state.auth
    nonce = secrets.token_urlsafe(16)
    base_path = request.app.state.runtime.settings.base_path.rstrip("/")

    # The child window uses BroadcastChannel to coordinate with the parent.
    script = f"""
const basePath = '{json.dumps(base_path)}';
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const id = params.get('channel') || '';
const status = document.getElementById('status');
if (!/^[a-f0-9-]{{36}}$/.test(id)) {{
  status.textContent = 'Open this window from an authorized Hermes tab.';
}} else {{
  const channel = new BroadcastChannel('hermes.webapp.window:' + id);
  let finished = false;
  const fail = message => {{
    if (finished) return;
    finished = true;
    status.textContent = message;
    channel.postMessage({{type: 'error'}});
    channel.close();
    clearTimeout(timeout);
  }};
  const timeout = setTimeout(() => fail('This window link expired. Open a new window from the original tab.'), 30000);
  channel.onmessage = async event => {{
    if (finished) return;
    if (event.data?.error) {{ fail(event.data.error); return; }}
    const ticket = event.data?.ticket;
    if (typeof ticket !== 'string' || !/^[A-Za-z0-9_-]{{43}}$/.test(ticket)) return;
    try {{
      const response = await fetch(basePath + '/webapp/window-session', {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ticket: ticket}})
      }});
      if (!response.ok) throw new Error('Window authorization expired.');
      const {{"token": token}} = await response.json();
      if (finished) return;
      if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{{43}}$/.test(token)) throw new Error('Invalid window authorization.');
      const key = 'hermes.webapp.session.v1:' + JSON.stringify([location.origin, basePath]);
      sessionStorage.setItem(key, token);
      const target = new URL(basePath + '/', location.origin);
      target.hash = params.get('route') || '/';
      channel.postMessage({{type: 'done'}});
      finished = true;
      clearTimeout(timeout);
      channel.close();
      location.replace(target.href);
    }} catch (error) {{
      fail(error.message || 'Could not authorize this window. Open it again from the original tab.');
    }}
  }};
  channel.postMessage({{type: 'ready'}});
}}
"""

    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<title>Opening Hermes</title><p id="status">Authorizing this window…</p>
<script nonce="{nonce}">{script}</script>""",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        },
    )


async def host_terminal_ws(websocket: WebSocket) -> None:
    """WebSocket handler for interactive host terminal."""
    runtime = websocket.scope.get("app").state.runtime
    auth = websocket.scope.get("app").state.auth

    # Check if host terminal is allowed
    if not host_terminal.request_allowed(runtime.settings):
        await websocket.close(code=4403, reason="Host terminal disabled")
        return

    # Check authentication
    if not auth.authenticated(websocket.scope, websocket=True):
        await websocket.close(code=4401, reason="Unauthorized")
        return

    log.info("host-terminal: connecting from %s", auth.client_address(websocket.scope))

    try:
        await host_terminal.handle_host_terminal_ws(websocket, runtime)
    except Exception as exc:
        log.exception("host-terminal: unhandled error")
        try:
            await websocket.close(code=1011, reason="Server error")
        except Exception:
            pass


routes = [
    Route("/api/webapp/window-ticket", issue_window_ticket, methods=["POST"]),
    Route("/webapp/window-session", redeem_window_ticket, methods=["POST"]),
    Route("/webapp/launch", launch_page, methods=["GET"]),
    Route("/webapp/window", child_window_page, methods=["GET"]),
    WebSocketRoute("/api/host-terminal", host_terminal_ws),
]
