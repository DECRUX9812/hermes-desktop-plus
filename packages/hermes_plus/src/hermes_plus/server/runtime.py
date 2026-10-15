"""Sidecar lifecycle and profile-scoped resources."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from hermes_plus import adapter
from hermes_plus.adapter import Capabilities, DashboardProcess, GatewayClient, probe_capabilities
from hermes_plus.server.auth import FrontDoorAuth
from hermes_plus.settings import ServeSettings
from hermes_plus.store import Store, open_store

log = logging.getLogger(__name__)


def _websocket_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    path = parts.path.rstrip("/")
    if not path.endswith("/api/ws"):
        path = f"{path}/api/ws" if path else "/api/ws"
    return urlunsplit((scheme, parts.netloc, path, parts.query, ""))


def _http_origin(url: str) -> str:
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in {"https", "wss"} else "http"
    return f"{scheme}://{parts.netloc}"


class Runtime:
    """Own the Hermes connection, optional child process, stores, and HTTP client."""

    def __init__(self, settings: ServeSettings) -> None:
        self.settings = settings
        self.auth = FrontDoorAuth(settings)
        self.install = None
        self.compat = None
        self.capabilities = Capabilities(None, set(), set(), {})
        self.dashboard: DashboardProcess | None = None
        self.internal: GatewayClient | None = None
        self.upstream_ws_url = ""
        self.upstream_http_origin = ""
        self.upstream_token = ""
        self.http_client: httpx.AsyncClient | None = None
        self._stores: dict[Path, Store] = {}
        self._stores_lock = threading.RLock()
        self.started = False

    async def start(self) -> None:
        if self.started:
            return
        try:
            self.install = await asyncio.to_thread(adapter.detect, self.settings.hermes_bin)
            self.compat = adapter.check_compat(self.install)
            if self.compat.status == "unsupported":
                log.warning("Running on unsupported Hermes installation: %s", self.compat.message)

            if self.settings.upstream_url:
                await self._attach()
            else:
                self.dashboard = DashboardProcess(
                    self.install,
                    port=0,
                    profile=self.settings.profile,
                )
                await asyncio.to_thread(self.dashboard.start)
                self.upstream_ws_url = self.dashboard.ws_url
                self.upstream_token = self.dashboard.token

            self.upstream_http_origin = _http_origin(self.upstream_ws_url)
            self.internal = GatewayClient(self.upstream_ws_url, self.upstream_token)
            await self.internal.connect()
            self.capabilities = await probe_capabilities(self.internal)
            self.http_client = httpx.AsyncClient(timeout=None, follow_redirects=False)
            self.started = True
            self._log_login_url()
        except BaseException:
            await self.stop()
            raise

    async def _attach(self) -> None:
        if self.settings.upstream_token_file is None:
            raise ValueError("Attaching to --upstream requires --upstream-token-file")
        self.upstream_token = self.settings.upstream_token_file.read_text(encoding="utf-8").strip()
        if not self.upstream_token:
            raise ValueError("Upstream token file is empty")
        self.upstream_ws_url = _websocket_url(self.settings.upstream_url or "")

    def _log_login_url(self) -> None:
        host = self.settings.host
        if self.settings.auth == "token":
            from urllib.parse import quote

            url = f"http://{host}:{self.settings.port}/?token={quote(self.auth.token)}"
        elif self.settings.auth == "password":
            url = f"http://{host}:{self.settings.port}/login"
        else:
            url = f"http://{host}:{self.settings.port}/"
        log.info("Hermes Plus login: %s", url)
        print(f"Hermes Plus login: {url}", flush=True)

    def store_for(self, profile: str | None = None) -> Store:
        selected = profile or self.settings.profile
        path = adapter.plus_home(selected).resolve()
        with self._stores_lock:
            store = self._stores.get(path)
            if store is None:
                store = open_store(path)
                self._stores[path] = store
            return store

    async def stop(self) -> None:
        internal, self.internal = self.internal, None
        if internal is not None:
            try:
                await internal.close()
            except Exception:
                log.exception("Failed to close the internal Hermes gateway client")

        http_client, self.http_client = self.http_client, None
        if http_client is not None:
            try:
                await http_client.aclose()
            except Exception:
                log.exception("Failed to close the sidecar HTTP client")

        with self._stores_lock:
            stores = list(self._stores.values())
            self._stores.clear()
        for store in stores:
            try:
                await asyncio.to_thread(store.close)
            except Exception:
                log.exception("Failed to close a Hermes Plus profile store")

        dashboard, self.dashboard = self.dashboard, None
        if dashboard is not None:
            try:
                await asyncio.to_thread(dashboard.stop)
            except Exception:
                log.exception("Failed to stop the Hermes dashboard child")
        self.started = False
