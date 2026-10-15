"""End-to-end sidecar tests: real hermes-plus subprocess on top of real stock Hermes.

Runs `hermes-plus serve` on 127.0.0.1:93xx with a throwaway HERMES_HOME and a tiny real
ui_dir, then exercises auth, injection, the WS multiplexer (local and forwarded RPCs),
the HTTP proxy, and child-process reaping. Skips with a reason when no stock Hermes
binary is available or the stock dashboard cannot start in this environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets

REPO_ROOT = Path(__file__).resolve().parents[4]
HERMES_PLUS_BIN = REPO_ROOT / ".venv" / "bin" / "hermes-plus"
HERMES_BIN = Path(
    os.environ.get("HERMES_PLUS_TEST_HERMES", "~/Code/ship-test/venv/bin/hermes")
).expanduser()

pytestmark = pytest.mark.integration


def _free_port() -> int:
    for port in range(9300, 9400):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in the 9300-9399 test range")


def _dashboard_processes(home: Path) -> list[tuple[int, str]]:
    """(pid, upstream session token) of dashboard children using this HERMES_HOME."""
    found: list[tuple[int, str]] = []
    marker = f"HERMES_HOME={home}".encode()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if b"dashboard" not in cmdline or marker not in environ:
            continue
        token = ""
        for item in environ.split(b"\0"):
            if item.startswith(b"HERMES_DASHBOARD_SESSION_TOKEN="):
                token = item.split(b"=", 1)[1].decode()
        found.append((int(entry.name), token))
    return found


class Sidecar:
    def __init__(self, home: Path, ui_dir: Path, port: int, token: str) -> None:
        self.home = home
        self.ui_dir = ui_dir
        self.port = port
        self.token = token
        self.url = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self._log = None

    @property
    def log_path(self) -> Path:
        return self.home / "sidecar.log"

    def start(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "HERMES_HOME": str(self.home),
                "HERMES_PLUS_TOKEN": self.token,
                "HERMES_PLUS_HERMES_BIN": str(HERMES_BIN),
                "HERMES_PLUS_UI_DIR": str(self.ui_dir),
            }
        )
        self._log = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            [str(HERMES_PLUS_BIN), "serve", "--host", "127.0.0.1", "--port", str(self.port)],
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self._close_log()
                log_text = self.log_path.read_text(errors="replace")[-4000:]
                if "ModuleNotFoundError" in log_text or "dashboard" in log_text:
                    pytest.skip(f"stock Hermes dashboard cannot start here:\n{log_text}")
                pytest.fail(f"hermes-plus exited with {self.proc.returncode}:\n{log_text}")
            try:
                reply = httpx.get(f"{self.url}/healthz", timeout=2)
                if reply.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        self.stop()
        pytest.fail(f"hermes-plus did not become ready:\n{self.log_path.read_text(errors='replace')[-4000:]}")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=10)
        self._close_log()

    def restart(self) -> None:
        self.stop()
        self.start()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None


@pytest.fixture(scope="session")
def sidecar(tmp_path_factory: pytest.TempPathFactory):
    if not HERMES_BIN.exists():
        pytest.skip(f"stock Hermes binary not available at {HERMES_BIN}")
    if not HERMES_PLUS_BIN.exists():
        pytest.skip("hermes-plus is not installed in the worktree venv")
    home = tmp_path_factory.mktemp("hermes-home")
    ui_dir = tmp_path_factory.mktemp("ui")
    (ui_dir / "index.html").write_text(
        "<!doctype html><html><head><title>demo</title></head><body>demo-ui</body></html>",
        encoding="utf-8",
    )
    (ui_dir / "assets").mkdir()
    (ui_dir / "assets" / "app-B12cD34e.js").write_text("console.log('demo');", encoding="utf-8")
    server = Sidecar(home, ui_dir, _free_port(), f"test-token-{secrets.token_hex(8)}")
    server.start()
    yield server
    server.stop()


async def _ws_open(sidecar: Sidecar, query: str = ""):
    url = f"ws://127.0.0.1:{sidecar.port}/api/ws?token={sidecar.token}{query}"
    ws = await websockets.connect(url)
    ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    assert ready.get("method") == "event"
    assert ready["params"]["type"] == "gateway.ready"
    return ws


async def _ws_call(ws: Any, method: str, params: dict | None = None, timeout: float = 30) -> dict:
    request_id = f"t-{uuid.uuid4().hex[:8]}"
    frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        frame["params"] = params
    await ws.send(json.dumps(frame))
    async with asyncio.timeout(timeout):
        async for raw in ws:
            reply = json.loads(raw)
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return reply
    raise TimeoutError(f"no reply for {method}")


def test_healthz_and_plus_info(sidecar: Sidecar):
    reply = httpx.get(f"{sidecar.url}/healthz", timeout=10)
    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "ok"
    assert "hermes_version" in body
    assert body["hermes_version"] or body["hermes_commit"]

    info = httpx.get(
        f"{sidecar.url}/api/plus/info",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert info.status_code == 200
    payload = info.json()
    assert payload["adapter_api"] == 1
    assert "hermes_version" in payload
    assert payload["compat"]["status"] in {"supported", "untested", "unsupported"}
    assert isinstance(payload["features"], dict)


def test_auth_token_flow(sidecar: Sidecar):
    with httpx.Client(follow_redirects=False, timeout=10) as client:
        assert client.get(f"{sidecar.url}/api/plus/info").status_code == 401
        assert client.get(f"{sidecar.url}/").status_code == 401

        login = client.get(f"{sidecar.url}/", params={"token": sidecar.token})
        assert login.status_code == 303
        assert "token=" not in login.headers["location"]
        cookie = login.headers["set-cookie"]
        assert "hermes_plus_session=" in cookie
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

        assert client.get(f"{sidecar.url}/").status_code == 200
        assert client.get(f"{sidecar.url}/api/plus/info").status_code == 200


@pytest.mark.asyncio
async def test_websocket_rejected_without_auth(sidecar: Sidecar):
    with pytest.raises(Exception) as excinfo:
        await websockets.connect(f"ws://127.0.0.1:{sidecar.port}/api/ws")
    assert type(excinfo.value).__name__ in {
        "InvalidStatus",
        "InvalidStatusCode",
        "ConnectionClosed",
        "ConnectionClosedError",
        "InvalidHandshake",
        "InvalidMessage",
    }


def test_index_injection_and_static_cache(sidecar: Sidecar):
    reply = httpx.get(
        f"{sidecar.url}/", headers={"X-Hermes-Session-Token": sidecar.token}, timeout=10
    )
    assert reply.status_code == 200
    html = reply.text
    assert f'window.__HERMES_SESSION_TOKEN__="{sidecar.token}";' in html
    assert "window.__HERMES_PLUS__={" in html
    assert "window.__HERMES_BASE_PATH__" in html
    upstream_tokens = [token for _, token in _dashboard_processes(sidecar.home) if token]
    assert upstream_tokens, "expected to discover the upstream dashboard token via /proc"
    for token in upstream_tokens:
        assert token not in html

    spa = httpx.get(
        f"{sidecar.url}/deep/client/route",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert spa.status_code == 200
    assert "__HERMES_SESSION_TOKEN__" in spa.text
    assert "no-cache" in spa.headers["cache-control"]

    asset = httpx.get(
        f"{sidecar.url}/assets/app-B12cD34e.js",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]

    missing = httpx.get(
        f"{sidecar.url}/assets/absent.js",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_stock_rpc_forwarded_over_multiplexer(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        reply = await _ws_call(ws, "config.get", {"key": "theme"})
    assert "error" not in reply, reply
    assert isinstance(reply["result"], dict)
    assert "value" in reply["result"]


@pytest.mark.asyncio
async def test_local_rpc_persists_across_restart(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        created = await _ws_call(
            ws, "bots_team.create", {"name": "Restart Demo", "mission": "Persist", "actor": "you"}
        )
        assert "error" not in created, created
        team_id = created["result"]["team"]["id"]
        listed = await _ws_call(ws, "bots_team.list")
        assert team_id in [team["id"] for team in listed["result"]["teams"]]

    sidecar.restart()
    assert httpx.get(f"{sidecar.url}/healthz", timeout=10).status_code == 200

    async with await _ws_open(sidecar) as ws:
        relisted = await _ws_call(ws, "bots_team.list")
    assert team_id in [team["id"] for team in relisted["result"]["teams"]]


@pytest.mark.asyncio
async def test_owned_method_reaches_upstream_with_namespaced_ids(sidecar: Sidecar):
    async with await _ws_open(sidecar) as ws:
        # Force the profile store to exist, then journal a pending approval by hand
        # (a real SQLite write; the server picks it up through its own connection).
        await _ws_call(ws, "fork.onboarding.state")
        db_path = sidecar.home / "hermes-plus" / "plus.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO approvals (request_id, session_id, created_at, status) "
                "VALUES ('req-itest', 'sess-itest', 1.0, 'pending')"
            )

        frames: list[dict] = []
        request_id = f"t-{uuid.uuid4().hex[:8]}"
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "fork.approvals.decide",
            "params": {"session_id": "sess-itest", "request_id": "req-itest", "choice": "once"},
        }))
        reply = None
        async with asyncio.timeout(30):
            async for raw in ws:
                frame = json.loads(raw)
                frames.append(frame)
                if isinstance(frame, dict) and frame.get("id") == request_id:
                    reply = frame
                    break

        assert reply is not None
        assert not any(
            str(frame.get("id", "")).startswith("hp-") for frame in frames
        ), f"internal namespaced frame leaked to the client: {frames}"
        if "error" in reply:
            # Upstream has no such live session; its error must reach our request id.
            assert reply["error"]["code"] not in {-32601, -32603}
        else:
            assert reply["result"]["resolved"] is False
            assert reply["result"]["approval"]["status"] == "allowed"


def test_http_api_proxy(sidecar: Sidecar):
    reply = httpx.get(
        f"{sidecar.url}/api/health",
        headers={"X-Hermes-Session-Token": sidecar.token},
        timeout=10,
    )
    assert reply.status_code == 200


def test_shutdown_reaps_dashboard_child(sidecar: Sidecar):
    children = _dashboard_processes(sidecar.home)
    assert children, "expected a running hermes dashboard child"
    sidecar.stop()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _dashboard_processes(sidecar.home):
        time.sleep(0.3)
    assert _dashboard_processes(sidecar.home) == []
