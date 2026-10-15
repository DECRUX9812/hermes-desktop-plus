"""Host terminal PTY policy and frame protocol handler."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Frame protocol: terminal I/O and metadata as JSON-framed records.
# Each frame: 4-byte big-endian length prefix + JSON object.
# Object types: {"type": "init", "rows": int, "cols": int, "cwd": str?, "shell": str?}
#              {"type": "input", "data": bytes (base64)}
#              {"type": "output", "data": bytes (base64)}
#              {"type": "resize", "rows": int, "cols": int}
#              {"type": "error", "message": str}
#              {"type": "close", "exit_code": int?}

META_PREFIX = "\x00HERMES_TERMINAL_META:"


def request_allowed(settings: Any) -> bool:
    """Allow host shells only when enabled and either authenticated or loopback."""
    if not settings.host_terminal:
        return False
    # Settings will have auth mode; for loopback-only, the auth middleware will allow it.
    return True


def shell_spec() -> tuple[list[str], str]:
    """Resolve the interactive shell command and name."""
    override = (os.environ.get("HERMES_DESKTOP_SHELL") or "").strip()
    if os.name != "nt":
        override = override or (os.environ.get("SHELL") or "").strip()

    command: str | None = None
    if override:
        command = _find_command(override)

    if os.name == "nt":
        if not command:
            command = _find_command("pwsh.exe") or _find_command("pwsh")
        if not command:
            system_root = (
                os.environ.get("SystemRoot")
                or os.environ.get("windir")
                or r"C:\Windows"
            )
            ps_path = str(
                Path(system_root)
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            command = _find_command(ps_path)
        command = command or _find_command("powershell.exe") or _find_command("cmd.exe") or "cmd.exe"
    else:
        if not command:
            for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
                if (resolved := _find_command(candidate)):
                    command = resolved
                    break
        command = command or "/bin/sh"

    name = Path(command).name.lower()
    if name.startswith(("pwsh", "powershell")):
        args = ["-NoLogo"]
    elif name.startswith("cmd"):
        args = []
    elif "zsh" in name or "bash" in name:
        args = ["-il"]
    else:
        args = ["-i"]

    return [command, *args], name


def _find_command(candidate: str) -> str | None:
    """Check if a command exists and is executable."""
    raw = (candidate or "").strip()
    if not raw:
        return None
    # Don't resolve relative paths; the PTY will chdir before exec.
    if not os.path.isabs(raw):
        return None
    try:
        if os.path.isfile(raw) and os.access(raw, os.X_OK):
            return raw
    except (OSError, ValueError):
        pass
    return None


def safe_cwd(requested: str | None) -> str:
    """Resolve and validate a working directory."""
    fallback = str(Path.home())
    try:
        candidate = Path((requested or "").strip() or fallback).expanduser().resolve()
        if candidate.is_dir():
            return str(candidate)
        if candidate.is_file():
            return str(candidate.parent)
    except (OSError, RuntimeError, ValueError):
        pass
    return fallback


def build_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build environment for the PTY process."""
    env = dict(os.environ)
    if base_env:
        env.update(base_env)

    # Clean up Node/npm variables that leak from our own process.
    for key in list(env):
        if key == "npm_config_prefix" or key.startswith(("npm_config_", "npm_package_")):
            env.pop(key, None)

    # Set terminal and locale.
    for key in ("NO_COLOR", "FORCE_COLOR", "COLORFGBG"):
        env.pop(key, None)
    env["COLORTERM"] = "truecolor"
    env["TERM"] = "xterm-256color"
    env["TERM_PROGRAM"] = "Hermes"
    env["HERMES_DESKTOP_TERMINAL"] = "1"
    env.setdefault("LC_CTYPE", "UTF-8")

    return env


async def handle_host_terminal_ws(websocket: Any, runtime: Any) -> None:
    """Handle a WebSocket connection for interactive host terminal."""
    try:
        await websocket.accept()
    except RuntimeError:
        return

    try:
        # First message must be init frame with rows/cols/cwd
        init_frame = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        try:
            msg = json.loads(init_frame)
        except (json.JSONDecodeError, ValueError):
            await websocket.send_json(
                {"type": "error", "message": "Invalid init frame"}
            )
            await websocket.close(code=1002, reason="Invalid init frame")
            return

        if msg.get("type") != "init":
            await websocket.send_json(
                {"type": "error", "message": "First frame must be type=init"}
            )
            await websocket.close(code=1002, reason="Expected init frame")
            return

        rows = int(msg.get("rows") or 24)
        cols = int(msg.get("cols") or 80)
        rows = max(2, min(rows, 200))
        cols = max(2, min(cols, 256))

        requested_cwd = msg.get("cwd")
        cwd = safe_cwd(requested_cwd)

        log.info("host-terminal: init rows=%d cols=%d cwd=%s", rows, cols, cwd)

        # Spawn PTY
        try:
            import ptyprocess
        except ImportError:
            await websocket.send_json(
                {"type": "error", "message": "ptyprocess not available"}
            )
            await websocket.close(code=1011, reason="PTY support unavailable")
            return

        argv, shell_name = shell_spec()
        env = build_env()

        try:
            proc = ptyprocess.PtyProcess.spawn(
                argv, cwd=cwd, env=env, dimensions=(rows, cols)
            )
        except (OSError, RuntimeError) as exc:
            log.exception("Failed to spawn PTY")
            await websocket.send_json(
                {"type": "error", "message": f"Failed to spawn shell: {exc}"}
            )
            await websocket.close(code=1011, reason="PTY spawn failed")
            return

        await websocket.send_json(
            {
                "type": "ready",
                "shell": shell_name,
                "rows": rows,
                "cols": cols,
                "cwd": cwd,
            }
        )

        # Manage I/O
        read_task = asyncio.create_task(_read_pty(proc, websocket))
        write_task = asyncio.create_task(_write_pty(proc, websocket))

        try:
            await asyncio.gather(read_task, write_task)
        finally:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                await asyncio.wait_for(asyncio.gather(read_task, write_task), timeout=1.0)
            except asyncio.TimeoutError:
                read_task.cancel()
                write_task.cancel()

    except asyncio.TimeoutError:
        log.warning("host-terminal: timeout waiting for init")
        await websocket.close(code=1000, reason="Timeout")
    except Exception as exc:
        log.exception("host-terminal: error")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
        try:
            await websocket.close(code=1011, reason="Server error")
        except Exception:
            pass


async def _read_pty(proc: Any, websocket: Any) -> None:
    """Read from PTY and send to WebSocket."""
    import base64

    loop = asyncio.get_event_loop()
    try:
        while True:
            # Read from PTY in a thread to avoid blocking
            data = await loop.run_in_executor(None, proc.read, 4096)
            if not data:
                break
            await websocket.send_json(
                {"type": "output", "data": base64.b64encode(data).decode("ascii")}
            )
    except Exception as exc:
        log.debug("_read_pty: %s", exc)
    finally:
        try:
            exit_code = proc.wait()
        except Exception:
            exit_code = None
        try:
            await websocket.send_json({"type": "close", "exit_code": exit_code})
        except Exception:
            pass


async def _write_pty(proc: Any, websocket: Any) -> None:
    """Read from WebSocket and write to PTY."""
    import base64

    loop = asyncio.get_event_loop()
    try:
        while True:
            msg = await websocket.receive_text()
            try:
                frame = json.loads(msg)
            except (json.JSONDecodeError, ValueError):
                continue

            msg_type = frame.get("type")
            if msg_type == "input":
                try:
                    data = base64.b64decode(frame.get("data", ""))
                    await loop.run_in_executor(None, proc.write, data)
                except Exception as exc:
                    log.debug("_write_pty input: %s", exc)
            elif msg_type == "resize":
                rows = int(frame.get("rows") or 24)
                cols = int(frame.get("cols") or 80)
                rows = max(2, min(rows, 200))
                cols = max(2, min(cols, 256))
                try:
                    proc.setwinsize(rows, cols)
                except Exception as exc:
                    log.debug("_write_pty resize: %s", exc)
            elif msg_type == "close":
                break
    except Exception as exc:
        log.debug("_write_pty: %s", exc)
