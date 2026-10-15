"""Webapp host terminal: OUR PTY in the user's home or the requested cwd.

This is not a proxy of upstream ``/api/pty`` — the fork's webapp terminal runs
a shell on the HOST under the server's own identity, so the sidecar owns the
PTY (ptyprocess) end to end and speaks the fork's frame protocol
(web_host_terminal.py + web_host_terminal_sessions.py):

  * first message: ``\\0HERMES_TERMINAL_META:`` + JSON ``{shell, terminalId,
    cwd, reconnected, retentionSeconds, truncated}``;
  * then raw terminal output frames (text);
  * inbound raw keystroke frames, resizes as ``\\x1b[RESIZE:<cols>;<rows>``;
  * ``?attach=<terminalId>`` reattaches a retained shell and replays the
    bounded buffer; ``?action=close&attach=`` closes it;
  * close codes: 4403 denied, 4410 expired, 1013 registry full.

Sessions survive viewer disconnects for RETENTION_SECONDS and die with the
process. Always requires auth (enforced by the middleware + route policy).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import select
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

META_PREFIX = "\0HERMES_TERMINAL_META:"
RETENTION_SECONDS = 15 * 60.0
MAX_SESSIONS = 16
BUFFER_CAP = 1024 * 1024
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")

_COLS_DEFAULT, _COLS_MAX = 80, 2000
_ROWS_DEFAULT, _ROWS_MAX = 24, 1000


def request_allowed(settings: Any) -> bool:
    """Allow host shells only when the setting enables them (default: loopback on)."""
    return bool(settings.host_terminal)


def _shell_command(candidate: str) -> str | None:
    """An executable path for ``candidate``; relative paths are refused."""
    raw = (candidate or "").strip()
    if not raw or (not raw.startswith("/") and os.name != "nt"):
        return None
    found = shutil.which(raw) or (raw if os.access(raw, os.X_OK) else None)
    return found


def shell_spec() -> tuple[list[str], str]:
    """Resolve the same interactive-shell ladder the fork and native Desktop use."""
    override = (os.environ.get("HERMES_DESKTOP_SHELL") or "").strip()
    override = override or (os.environ.get("SHELL") or "").strip()
    command = _shell_command(override)
    if not command:
        command = next(
            (
                resolved
                for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh")
                if (resolved := _shell_command(candidate))
            ),
            "/bin/sh",
        )
    name = Path(command).name.lower()
    args = ["-il"] if "zsh" in name or "bash" in name else ["-i"]
    return [command, *args], name


def safe_cwd(requested: str | None) -> str:
    fallback = Path.home()
    try:
        candidate = Path((requested or "").strip() or fallback).expanduser().resolve()
        if candidate.is_dir():
            return str(candidate)
        if candidate.is_file():
            return str(candidate.parent)
    except (OSError, RuntimeError, ValueError):
        pass
    return str(fallback)


def build_env() -> dict[str, str]:
    """A clean child env: no npm leakage, truecolor xterm (fork web_host_terminal.py:120-137)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(
        ("npm_config_", "npm_package_"))}
    for key in ("NO_COLOR", "FORCE_COLOR", "COLORFGBG"):
        env.pop(key, None)
    env["COLORTERM"] = "truecolor"
    env["TERM"] = "xterm-256color"
    env["TERM_PROGRAM"] = "Hermes Plus"
    env.setdefault("LC_CTYPE", "UTF-8")
    return env


def query_dimension(raw: Any, default: int, maximum: int) -> int:
    try:
        value = int(raw or default)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(2, min(maximum, value))


class _Session:
    """One retained host shell: ptyprocess owned here, one attached viewer."""

    def __init__(self, token: str, proc: Any, shell: str, cwd: str) -> None:
        self.token = token
        self.proc = proc
        self.shell = shell
        self.cwd = cwd
        self.buffer = bytearray()
        self.truncated = False
        self.websocket: Any = None
        self.last_detached_at: float | None = None
        self.closed = False

    def _append(self, chunk: bytes) -> None:
        self.buffer.extend(chunk)
        if len(self.buffer) > BUFFER_CAP:
            del self.buffer[: len(self.buffer) - BUFFER_CAP]
            self.truncated = True

    def _read_blocking(self) -> bytes:
        if self.closed or not self.proc.isalive():
            return b""
        ready, _, _ = select.select([self.proc.fd], [], [], 0.5)
        return os.read(self.proc.fd, 65536) if ready else b""

    def metadata(self, *, reconnected: bool) -> str:
        return META_PREFIX + json.dumps({
            "shell": self.shell, "terminalId": self.token, "cwd": self.cwd,
            "reconnected": reconnected, "retentionSeconds": RETENTION_SECONDS,
            "truncated": self.truncated,
        }, separators=(",", ":"))

    async def run(self) -> None:
        """Drain the PTY into the buffer and the attached viewer until exit."""
        loop = asyncio.get_running_loop()
        while not self.closed:
            chunk = await loop.run_in_executor(None, self._read_blocking)
            if not chunk:
                if not self.proc.isalive():
                    break
                continue
            self._append(chunk)
            viewer = self.websocket
            if viewer is not None:
                try:
                    await viewer.send_text(chunk.decode("utf-8", "replace"))
                except Exception:
                    pass
        await registry.remove(self.token)


class HostTerminalRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._tasks: set[asyncio.Task] = set()
        self._reaper: asyncio.Task | None = None

    def start(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap(), name="hermes-plus-host-terminal-reaper")

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            for token, session in list(self._sessions.items()):
                if session.closed or not session.proc.isalive() or (
                    session.websocket is None
                    and session.last_detached_at is not None
                    and now - session.last_detached_at > RETENTION_SECONDS
                ):
                    await self.remove(token)

    async def create(self, websocket: Any, requested_cwd: str | None, cols: int, rows: int) -> _Session:
        from ptyprocess import PtyProcess

        if len(self._sessions) >= MAX_SESSIONS:
            raise _RegistryFull()
        argv, shell = shell_spec()
        cwd = safe_cwd(requested_cwd)
        token = secrets.token_urlsafe(32)
        try:
            proc = PtyProcess.spawn(
                argv, cwd=cwd, env=build_env(), dimensions=(rows, cols))
        except (OSError, ImportError) as exc:
            raise _PtyUnavailable(str(exc)) from exc
        session = _Session(token, proc, shell, cwd)
        self._sessions[token] = session
        self.start()
        task = asyncio.create_task(session.run(), name=f"hermes-plus-pty-{token[:8]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return session

    async def resolve(self, token: str) -> _Session | None:
        session = self._sessions.get(token)
        if session is None or session.closed:
            return None
        return session

    async def remove(self, token: str) -> None:
        session = self._sessions.pop(token, None)
        if session is None:
            return
        session.closed = True
        viewer, session.websocket = session.websocket, None
        if not session.proc.isalive():
            if viewer is not None:
                await _send_closed(viewer, token)
        else:
            try:
                session.proc.terminate(force=True)
            except Exception:
                pass
            if viewer is not None:
                await _send_closed(viewer, token)
        try:
            session.proc.close(force=True)
        except Exception:
            pass


class _RegistryFull(Exception):
    pass


class _PtyUnavailable(Exception):
    pass


registry = HostTerminalRegistry()


async def _send_closed(websocket: Any, token: str) -> None:
    try:
        await websocket.send_text(META_PREFIX + json.dumps({"terminalId": token, "closed": True}))
        await websocket.close(code=1000)
    except Exception:
        pass


async def handle_host_terminal_ws(websocket: Any) -> None:
    """Route handler; auth was enforced by the middleware, policy is checked here."""
    runtime = websocket.app.state.runtime
    if not request_allowed(runtime.settings):
        await websocket.close(code=4403, reason="host terminal requires authenticated Webapp")
        return

    params = websocket.query_params
    token = params.get("attach")
    action = params.get("action")
    if action not in (None, "close"):
        await websocket.close(code=4403, reason="Terminal belongs to another identity or profile")
        return
    if token is not None and not TOKEN_RE.fullmatch(token):
        await websocket.close(code=4410, reason="Terminal or profile expired; open a new terminal")
        return

    if action == "close":
        if token is not None:
            await registry.remove(token)
        else:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        await _send_closed(websocket, token)
        return

    cols = query_dimension(params.get("cols"), _COLS_DEFAULT, _COLS_MAX)
    rows = query_dimension(params.get("rows"), _ROWS_DEFAULT, _ROWS_MAX)
    reconnected = token is not None
    if token is None:
        try:
            session = await registry.create(websocket, params.get("cwd"), cols, rows)
        except _RegistryFull:
            await websocket.close(code=1013, reason="Terminal capacity reached; close a terminal and retry")
            return
        except _PtyUnavailable as exc:
            await websocket.accept()
            await websocket.send_text(META_PREFIX + json.dumps({"error": str(exc)}))
            await websocket.close(code=1011, reason="Pseudo-terminal support is not available")
            return
        await websocket.accept()
    else:
        session = await registry.resolve(token)
        if session is None:
            await websocket.close(code=4410, reason="Terminal or profile expired; open a new terminal")
            return
        await websocket.accept()

    session.last_detached_at = None
    session.websocket = websocket
    try:
        await websocket.send_text(session.metadata(reconnected=reconnected))
        if reconnected and session.buffer:
            await websocket.send_text(session.buffer.decode("utf-8", "replace"))
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            raw = message.get("bytes")
            if raw is None and message.get("text") is not None:
                raw = message["text"].encode("utf-8")
            if raw is None:
                continue
            if (match := RESIZE_RE.fullmatch(raw)) is not None:
                try:
                    session.proc.setwinsize(int(match[2]), int(match[1]))
                except Exception:
                    pass
                continue
            if not session.proc.isalive():
                return
            session.proc.write(raw)
    except Exception:
        pass
    finally:
        if session.websocket is websocket:
            session.websocket = None
            session.last_detached_at = time.monotonic()
