"""Sidecar lifecycle and profile-scoped resources."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from hermes_plus import adapter
from hermes_plus.adapter import Capabilities, DashboardProcess, GatewayClient, probe_capabilities
from hermes_plus.adapter.mcp import McpClientManager
from hermes_plus.adapter.protocol import EVENT_NAMES
from hermes_plus.server.auth import FrontDoorAuth
from hermes_plus.server.spool import SpoolIngester
from hermes_plus.settings import ServeSettings
from hermes_plus.store import Store, open_store

log = logging.getLogger(__name__)

SIDECAR_FILE = "sidecar.json"
_SPOOL_INTERVAL_SECONDS = 2.0
_WATCHDOG_INTERVAL_SECONDS = 10.0


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
        self.capabilities = Capabilities(None, set(), set(), {}, known=False)
        self.dashboard: DashboardProcess | None = None
        self.internal: GatewayClient | None = None
        self.upstream_ws_url = ""
        self.upstream_http_origin = ""
        self.upstream_token = ""
        self.http_client: httpx.AsyncClient | None = None
        self.mcp = McpClientManager()
        self._stores: dict[Path, Store] = {}
        self._stores_lock = threading.RLock()
        self._clients: set[tuple[Any, asyncio.Lock]] = set()
        self._spool_task: asyncio.Task[None] | None = None
        self._spool_ingester: SpoolIngester | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self.started = False

    def register_client(self, client: tuple[Any, asyncio.Lock]) -> None:
        self._clients.add(client)

    def unregister_client(self, client: tuple[Any, asyncio.Lock]) -> None:
        self._clients.discard(client)

    async def broadcast(
        self, event_name: str, payload: dict[str, Any], session_id: str | None = None
    ) -> None:
        event_params: dict[str, Any] = {"type": event_name, "payload": payload}
        if session_id:
            event_params["session_id"] = session_id
        message = {"jsonrpc": "2.0", "method": "event", "params": event_params}
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for ws, lock in list(self._clients):
            try:
                async with lock:
                    await ws.send_text(raw)
            except Exception:
                pass

    async def emit(
        self, event_name: str, payload: dict[str, Any], session_id: str | None = None
    ) -> None:
        await self.broadcast(event_name, payload, session_id=session_id)

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
            try:
                self.capabilities = await probe_capabilities(self.internal)
            except (TimeoutError, OSError, RuntimeError) as exc:
                # A slow, wedged, or never-answering upstream must not take the
                # sidecar down. known=False makes owns() forward every
                # shared-namespace method upstream (a local handler there would
                # shadow a stock method we never saw) while plus-only
                # namespaces keep working. There is no re-probe on reconnect:
                # restart the sidecar to re-probe.
                log.warning(
                    "upstream capability probe failed (%s); serving with capabilities "
                    "unknown — shared-namespace calls forward to Hermes; restart the "
                    "sidecar to re-probe", exc)
                self.capabilities = Capabilities(
                    version=None, methods=set(), events=set(EVENT_NAMES), features={},
                    known=False,
                )
            self.http_client = httpx.AsyncClient(timeout=None, follow_redirects=False)
            self._write_sidecar_json()
            self._start_spool_ingester()
            if self.dashboard is not None:
                self._watchdog_task = asyncio.create_task(
                    self._dashboard_watchdog(), name="hermes-plus-dashboard-watchdog")
            self.started = True
            self._log_login_url()
        except BaseException:
            await self.stop()
            raise

    def _write_sidecar_json(self) -> None:
        """Contact card for the bridge plugin's tools: loopback URL, token, pid. Mode 0600."""
        plus_home = adapter.plus_home(self.settings.profile)
        body = json.dumps({
            "url": f"http://127.0.0.1:{self.settings.port}",
            "token": self.auth.token,
            "pid": os.getpid(),
        })
        fd = os.open(plus_home / SIDECAR_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, body.encode("utf-8"))
        finally:
            os.close(fd)

    def _remove_sidecar_json(self) -> None:
        try:
            (adapter.plus_home(self.settings.profile) / SIDECAR_FILE).unlink()
        except OSError:
            pass

    def _start_spool_ingester(self) -> None:
        plus_home = adapter.plus_home(self.settings.profile)
        self._spool_ingester = SpoolIngester(self.store_for(self.settings.profile), plus_home)
        self._spool_task = asyncio.create_task(self._spool_loop(), name="hermes-plus-spool-ingest")

    async def _spool_loop(self) -> None:
        assert self._spool_ingester is not None
        while True:
            await asyncio.sleep(_SPOOL_INTERVAL_SECONDS)
            try:
                await asyncio.to_thread(self._spool_ingester.ingest_once)
            except Exception:
                log.exception("spool ingest pass failed")

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


    # ---- dashboard self-heal -------------------------------------------------
    # `hermes update` (and the in-app update chip) restarts every dashboard of the
    # updated home so the backend matches the new frontend. It relaunches our child
    # with the same argv but without HERMES_DASHBOARD_SESSION_TOKEN, so the new
    # process mints its own token and every proxied call gets 401. The watchdog
    # notices (child gone, or our token rejected) and starts a fresh child with our
    # token, so an update needs no manual restart of `hermes-plus serve`.

    async def _dashboard_watchdog(self) -> None:
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
            try:
                if await self._dashboard_needs_respawn():
                    await self._respawn_dashboard()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dashboard watchdog pass failed")

    async def _dashboard_needs_respawn(self) -> bool:
        dashboard = self.dashboard
        if dashboard is None:
            return False
        if dashboard.process is None or dashboard.process.poll() is not None:
            log.warning("the stock Hermes dashboard child exited")
            return True
        if self.http_client is None:
            return False
        try:
            response = await self.http_client.get(
                f"{self.upstream_http_origin}/api/profiles/active",
                headers={"X-Hermes-Session-Token": self.upstream_token},
                timeout=10,
            )
        except httpx.HTTPError:
            return False
        if response.status_code == 401:
            log.warning("the stock Hermes dashboard rejected our session token")
            return True
        return False

    async def _respawn_dashboard(self) -> None:
        old = self.dashboard
        assert old is not None and self.install is not None
        old_port = old.port
        home = old._home()
        await asyncio.to_thread(old.stop)
        await asyncio.to_thread(_reap_stray_dashboard, old_port, home)
        fresh = DashboardProcess(self.install, port=0, token=old.token, profile=self.settings.profile)
        await asyncio.to_thread(fresh.start)
        self.dashboard = fresh
        self.upstream_ws_url = fresh.ws_url
        self.upstream_token = fresh.token
        self.upstream_http_origin = _http_origin(self.upstream_ws_url)
        internal = GatewayClient(self.upstream_ws_url, self.upstream_token)
        await internal.connect()
        previous, self.internal = self.internal, internal
        if previous is not None:
            try:
                await previous.close()
            except Exception:
                log.exception("Failed to close the previous internal gateway client")
        log.warning("respawned the stock Hermes dashboard on port %d (was %d)", fresh.port, old_port)

    async def stop(self) -> None:
        self._remove_sidecar_json()
        watchdog, self._watchdog_task = self._watchdog_task, None
        if watchdog is not None:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
        task, self._spool_task = self._spool_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
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

        try:
            await self.mcp.close_all()
        except Exception:
            log.exception("Failed to close MCP client connections")

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


def _reap_stray_dashboard(port: int, home: Path) -> None:
    """Stop a dashboard someone else relaunched on our old port for OUR home only (Linux)."""
    proc = Path("/proc")
    if not proc.is_dir() or not port:
        return
    import signal

    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            env = dict(
                item.split(b"=", 1) for item in (entry / "environ").read_bytes().split(b"\0") if b"=" in item
            )
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in argv]
        if "dashboard" not in args or "--port" not in args:
            continue
        index = args.index("--port")
        if index + 1 >= len(args) or args[index + 1] != str(port):
            continue
        try:
            same_home = Path(env.get(b"HERMES_HOME", b"").decode()).resolve() == home.resolve()
        except (OSError, RuntimeError):
            same_home = False
        if not same_home:
            continue
        try:
            os.kill(int(entry.name), signal.SIGTERM)
            log.info("stopped stray dashboard pid %s on port %d", entry.name, port)
        except OSError:
            pass
