"""ASGI app assembly: health, info, our RPC multiplexer, proxies, and the SPA."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from hermes_plus import __version__, adapter
from hermes_plus.http import ROUTERS, files, uploads
from hermes_plus.server import multiplexer, proxy, static
from hermes_plus.server import auth as auth_module
from hermes_plus.server.auth import AuthMiddleware, FrontDoorAuth, login_page
from hermes_plus.server.bridge_rpc import bridge_rpc
from hermes_plus.server.hygiene import (
    AccessLogMiddleware,
    SecurityHeadersMiddleware,
    install_log_filters,
)
from hermes_plus.server.runtime import Runtime
from hermes_plus.settings import ServeSettings

log = logging.getLogger(__name__)

WS_PROXY_PATHS = ("/api/pty", "/api/events", "/api/pub", "/api/console", "/api/display/ws")
_API_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def build_app(settings: ServeSettings, runtime: Runtime) -> Starlette:
    async def healthz(request: Any) -> JSONResponse:
        install = runtime.install
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "hermes_version": getattr(install, "version", None),
                "hermes_commit": getattr(install, "upstream_commit", None),
                "compat": getattr(runtime.compat, "status", "unknown"),
            }
        )

    async def plus_info(request: Any) -> JSONResponse:
        install = runtime.install
        compat = runtime.compat
        return JSONResponse(
            {
                "version": __version__,
                "adapter_api": adapter.ADAPTER_API,
                "hermes_version": getattr(install, "version", None),
                "hermes_commit": getattr(install, "upstream_commit", None),
                "compat": {
                    "status": getattr(compat, "status", "unknown"),
                    "message": getattr(compat, "message", ""),
                },
                # "unknown" = the startup probe failed; the UI/doctor banners it.
                "capabilities": (
                    "unknown" if not getattr(runtime.capabilities, "known", True) else "known"
                ),
                "features": dict(runtime.capabilities.features),
            }
        )

    async def ws_ticket(request: Any) -> JSONResponse:
        """Mint the single-use credential the renderer dials /api/ws with.

        Upstream's dashboard serves this route for cookie-authenticated
        clients; the sidecar is the front door, so it mints its own.
        """
        auth: FrontDoorAuth = request.app.state.auth
        return JSONResponse(
            {
                "ticket": auth.mint_ws_ticket(),
                "ttl_seconds": auth_module.WS_TICKET_TTL_SECONDS,
            }
        )

    async def ws_rpc(websocket: Any) -> None:
        await multiplexer.handle_websocket(websocket, runtime)

    def ws_proxy(path: str):
        async def handler(websocket: Any) -> None:
            await proxy.proxy_websocket(websocket, runtime, path)

        return handler

    async def plugin_ws_proxy(websocket: Any) -> None:
        # Stock dashboard plugins (e.g. kanban's live board) open their own
        # sockets under /api/plugins/<name>/...; forward them like the core ones.
        await proxy.proxy_websocket(websocket, runtime, websocket.url.path)

    async def api_proxy(request: Any) -> Any:
        return await proxy.proxy_http(
            request, runtime, runtime.upstream_http_origin, auth_upstream=True
        )

    async def spa(request: Any) -> Any:
        return await static.serve(request, settings, runtime)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    routes: list[Any] = [
        Route("/healthz", healthz),
        Route("/api/plus/info", plus_info),
        Route("/api/plus/bridge/rpc", bridge_rpc, methods=["POST"]),
        Route("/api/auth/ws-ticket", ws_ticket, methods=["POST"]),
    ]
    if settings.auth == "password":
        routes.append(Route("/login", login_page, methods=["GET", "POST"]))
    routes.extend(ROUTERS)
    routes.append(WebSocketRoute("/api/ws", ws_rpc))
    routes.extend(WebSocketRoute(path, ws_proxy(path)) for path in WS_PROXY_PATHS)
    routes.append(WebSocketRoute("/api/plugins/{path:path}", plugin_ws_proxy))
    routes.append(Route("/api/{path:path}", api_proxy, methods=_API_METHODS))
    routes.append(Route("/{path:path}", spa, methods=["GET", "HEAD"]))

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[
            # Outermost first: the access log records the status every other
            # layer produced, and the redaction it does is why uvicorn's own
            # access log (which prints the raw query string, token included) is
            # turned off in run().
            Middleware(AccessLogMiddleware, token=runtime.auth.token),
            Middleware(SecurityHeadersMiddleware),
            Middleware(AuthMiddleware, auth=runtime.auth),
            Middleware(uploads.ChatFileUploadLimitMiddleware),
        ],
    )
    app.state.auth = runtime.auth
    app.state.runtime = runtime
    # File tickets are admitted by the front-door auth (fork auth_middleware seam).
    auth_module.file_ticket_redeem = files.redeem_scope
    return app


def run(settings: ServeSettings) -> None:
    """Serve the sidecar with graceful SIGTERM/SIGINT shutdown."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    install_log_filters()
    runtime = Runtime(settings)
    app = build_app(settings, runtime)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        # Uvicorn logs the whole request target, and the target of the printed
        # one-time login URL is ``/?token=<session token>``. Our own
        # AccessLogMiddleware logs the same line with the query redacted.
        access_log=False,
    )
    uvicorn.Server(config).run()
