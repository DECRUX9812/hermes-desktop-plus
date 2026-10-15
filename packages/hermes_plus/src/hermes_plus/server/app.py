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
from hermes_plus.http import ROUTERS
from hermes_plus.server import multiplexer, proxy, static
from hermes_plus.server.auth import AuthMiddleware, login_page
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
                "features": dict(runtime.capabilities.features),
            }
        )

    async def ws_rpc(websocket: Any) -> None:
        await multiplexer.handle_websocket(websocket, runtime)

    def ws_proxy(path: str):
        async def handler(websocket: Any) -> None:
            await proxy.proxy_websocket(websocket, runtime, path)

        return handler

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
    ]
    if settings.auth == "password":
        routes.append(Route("/login", login_page, methods=["GET", "POST"]))
    routes.extend(ROUTERS)
    routes.append(WebSocketRoute("/api/ws", ws_rpc))
    routes.extend(WebSocketRoute(path, ws_proxy(path)) for path in WS_PROXY_PATHS)
    routes.append(Route("/api/{path:path}", api_proxy, methods=_API_METHODS))
    routes.append(Route("/{path:path}", spa, methods=["GET", "HEAD"]))

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(AuthMiddleware, auth=runtime.auth)],
    )
    app.state.auth = runtime.auth
    app.state.runtime = runtime
    return app


def run(settings: ServeSettings) -> None:
    """Serve the sidecar with graceful SIGTERM/SIGINT shutdown."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime = Runtime(settings)
    app = build_app(settings, runtime)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
    uvicorn.Server(config).run()
