"""Shared fixtures for integration tests: a real hermes-plus serve subprocess
over real stock Hermes (moved here from test_server_sidecar.py so the HTTP
route tests reuse the same session-scoped sidecar)."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import socket
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


def free_port() -> int:
    for port in range(9300, 9400):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in the 9300-9399 test range")


class Sidecar:
    def __init__(self, home: Path, ui_dir: Path, port: int, token: str, env_extra: dict[str, str] | None = None) -> None:
        self.home = home
        self.ui_dir = ui_dir
        self.port = port
        self.token = token
        self.env_extra = env_extra or {}
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
        env.update(self.env_extra)
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


def make_sidecar(tmp_path: Path, env_extra: dict[str, str] | None = None) -> Sidecar:
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "hermes-home").mkdir(parents=True, exist_ok=True)
    (ui_dir / "index.html").write_text(
        "<!doctype html><html><head><title>demo</title></head><body>demo-ui</body></html>",
        encoding="utf-8",
    )
    (ui_dir / "assets").mkdir(exist_ok=True)
    (ui_dir / "assets" / "app-B12cD34e.js").write_text("console.log('demo');", encoding="utf-8")
    server = Sidecar(
        tmp_path / "hermes-home", ui_dir, free_port(), f"test-token-{secrets.token_hex(8)}",
        env_extra=env_extra,
    )
    server.start()
    return server


@pytest.fixture(scope="session")
def sidecar(tmp_path_factory: pytest.TempPathFactory):
    if not HERMES_BIN.exists():
        pytest.skip(f"stock Hermes binary not available at {HERMES_BIN}")
    if not HERMES_PLUS_BIN.exists():
        pytest.skip("hermes-plus is not installed in the worktree venv")
    server = make_sidecar(tmp_path_factory.mktemp("hermes-home"))
    yield server
    server.stop()


async def ws_open(sidecar: Sidecar, query: str = ""):
    url = f"ws://127.0.0.1:{sidecar.port}/api/ws?token={sidecar.token}{query}"
    ws = await websockets.connect(url)
    ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    assert ready.get("method") == "event"
    assert ready["params"]["type"] == "gateway.ready"
    return ws


_ws_open = ws_open


async def ws_call(ws: Any, method: str, params: dict | None = None, timeout: float = 30) -> dict:
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


_ws_call = ws_call


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


def seed_stock_session(stock_hermes: str, home: Path, session_id: str, messages: list[dict[str, Any]]) -> None:
    """Write a session + transcript into the stock SessionDB (state.db) with the
    stock venv's own python, the way the fork's desktop rows are stored."""
    python = Path(stock_hermes).with_name("python3")
    script = ("import json, sys\n"
              "from hermes_state import SessionDB\n"
              "db = SessionDB()\n"
              "db.ensure_session(sys.argv[1], source='desktop')\n"
              "db.set_session_title(sys.argv[1], 'Shared demo ' + sys.argv[1][-6:])\n"
              "db.append_messages_batch(sys.argv[1], json.loads(sys.argv[2]))\n"
              "db.close()\n")
    result = subprocess.run(
        [str(python), "-c", script, session_id, json.dumps(messages)],
        env={**os.environ, "HERMES_HOME": str(home)}, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
