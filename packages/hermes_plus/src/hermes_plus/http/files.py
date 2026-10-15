"""Path-bound tickets for browser file URLs (fork web_routers/files.py + web_server_file_tickets.py).

A download link or an ``<audio>``/``<video>`` source cannot send the session
header, and the session token grants file access, so it must never appear in a
URL the browser records. An authenticated ``POST /api/files/ticket`` mints a
ticket that admits GET/HEAD of exactly one route + query and nothing else.
Downloads are single-use; stream tickets are reusable until they expire.
In-memory only: tickets die with the process, like the token.

The auth middleware consults ``redeem_scope`` (wired by the app assembly) so a
ticket alone authenticates its exact URL — the fork's auth_middleware seam.
``/api/files/*`` is otherwise STOCK surface and proxies to upstream unchanged;
the ticketed GET/HEAD of download/stream forwards to the stock route with the
internal upstream token, passing ``Range``/``If-Range`` through and streaming
the stock 200/206 back.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger(__name__)

DOWNLOAD_TTL_SECONDS = 60
#: A transcript video may be played long after it mounted; seeking past the
#: buffered range needs the ticket again.
STREAM_TTL_SECONDS = 60 * 60
_MAX_TICKETS = 1024

FILE_ROUTES: dict[str, str] = {"download": "/api/files/download", "stream": "/api/files/stream"}
TICKETED_PATHS = frozenset(FILE_ROUTES.values())


@dataclass(frozen=True)
class _Ticket:
    scope: tuple[str, tuple[tuple[str, str], ...]]
    expires_at: float
    single_use: bool


_lock = threading.Lock()
_tickets: dict[str, _Ticket] = {}


def mint(route: str, query: dict[str, str]) -> str:
    """Ticket for GET/HEAD of ``FILE_ROUTES[route]`` with exactly ``query`` (plus ``ticket``)."""
    route_path = FILE_ROUTES[route]
    single_use = route == "download"
    now = time.monotonic()
    ticket = secrets.token_urlsafe(32)
    entry = _Ticket(
        scope=(route_path, tuple(sorted(query.items()))),
        expires_at=now + (DOWNLOAD_TTL_SECONDS if single_use else STREAM_TTL_SECONDS),
        single_use=single_use,
    )
    with _lock:
        for stale in [key for key, value in _tickets.items() if value.expires_at <= now]:
            del _tickets[stale]
        while len(_tickets) >= _MAX_TICKETS:
            del _tickets[next(iter(_tickets))]
        _tickets[ticket] = entry
    return ticket


def redeem_query(route_path: str, query: list[tuple[str, str]], ticket: str, method: str) -> bool:
    """True when ``ticket`` authorizes GET/HEAD of this exact route + query; a
    download ticket is consumed by a successful redeem."""
    if not ticket or method not in ("GET", "HEAD"):
        return False
    scope = (route_path, tuple(sorted((key, value) for key, value in query if key != "ticket")))
    with _lock:
        entry = _tickets.get(ticket)
        if entry is None or entry.scope != scope or entry.expires_at <= time.monotonic():
            return False
        if entry.single_use:
            del _tickets[ticket]
    return True


def redeem_scope(scope: dict[str, Any]) -> bool:
    """Middleware hook: admit a GET/HEAD whose ``?ticket=`` covers exactly this URL."""
    if scope.get("method") not in ("GET", "HEAD") or scope.get("path", "") not in TICKETED_PATHS:
        return False
    query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
    ticket = dict(query).get("ticket", "")
    return redeem_query(scope["path"], query, ticket, scope.get("method", ""))


async def issue_file_ticket(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    route = str(payload.get("route") or "")
    path = str(payload.get("path") or "")
    if route not in FILE_ROUTES:
        return JSONResponse({"detail": "route must be download or stream"}, status_code=400)
    if not path:
        return JSONResponse({"detail": "path required"}, status_code=400)
    query = {
        key: str(payload[key])
        for key in ("path", "profile", "session_id")
        if payload.get(key) is not None
    }
    ticket = mint(route, query)
    return JSONResponse({"ticket": ticket}, headers={"Cache-Control": "no-store"})


async def _proxy_files(request: Request, route_path: str) -> Response:
    """Forward the request to the STOCK files route with the internal token.

    ``proxy_http`` forwards Range/If-Range and streams the stock 200/206 back.
    A HEAD of download is sent upstream as GET (stock serves GET only there)
    with the body discarded, keeping stock's exact status and headers.
    """
    runtime = request.app.state.runtime
    discard_body = route_path == "/api/files/download" and request.method == "HEAD"

    from hermes_plus.server import proxy

    scope = dict(request.scope)
    scope["method"] = "GET" if discard_body else request.method
    scope["path"] = route_path
    scope["query_string"] = urlencode(
        [(key, value) for key, value in request.query_params.multi_items() if key != "ticket"]
    ).encode()
    forwarded = Request(scope, receive=request.receive)

    if not discard_body:
        return await proxy.proxy_http(
            forwarded, runtime, runtime.upstream_http_origin, auth_upstream=True
        )

    client = runtime.http_client
    upstream_request = client.build_request(
        "GET",
        f"{runtime.upstream_http_origin}{route_path}?{scope['query_string'].decode()}",
        headers={"X-Hermes-Session-Token": runtime.upstream_token},
    )
    upstream = await client.send(upstream_request, stream=True)
    try:
        upstream_headers = Headers(
            raw=[
                (key.encode("latin-1"), value.encode("latin-1"))
                for key, value in upstream.headers.items()
                if key.lower() not in proxy._HOP_HEADERS
            ]
        )
        status = upstream.status_code
    finally:
        await upstream.aclose()
    return Response(status_code=status, headers=upstream_headers)


async def download_managed_file(request: Request) -> Response:
    return await _proxy_files(request, "/api/files/download")


async def stream_managed_file(request: Request) -> Response:
    return await _proxy_files(request, "/api/files/stream")


routes = [
    Route("/api/files/ticket", issue_file_ticket, methods=["POST"]),
    Route("/api/files/download", download_managed_file, methods=["GET", "HEAD"]),
    Route("/api/files/stream", stream_managed_file, methods=["GET", "HEAD"]),
]
