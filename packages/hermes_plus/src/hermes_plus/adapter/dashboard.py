"""Lifecycle management for a stock Hermes dashboard child."""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from hermes_plus.adapter.paths import hermes_home
from hermes_plus.adapter.version import HermesInstall


class DashboardProcess:
    """Start the stock dashboard on loopback and manage its process group."""

    def __init__(
        self,
        inst: HermesInstall,
        *,
        port: int = 0,
        token: str | None = None,
        profile: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.inst = inst
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self.profile = profile
        self.extra_env = dict(extra_env or {})
        self.process: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self.url = ""
        self.ws_url = ""

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _home(self) -> Path:
        if self.extra_env.get("HERMES_HOME"):
            return Path(os.path.expandvars(os.path.expanduser(self.extra_env["HERMES_HOME"])))
        if self.profile is not None:
            return hermes_home(self.profile)
        return self.inst.hermes_home

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("HERMES_WEB_DIST", None)
        env.update(self.extra_env)
        env["HERMES_HOME"] = str(self._home())
        env["HERMES_DASHBOARD_SESSION_TOKEN"] = self.token
        return env

    def start(self, timeout: float = 60) -> None:
        """Start dashboard and wait for the public liveness endpoint."""
        if self.process is not None and self.process.poll() is None:
            return
        self.port = self.port or self._free_port()
        home = self._home()
        log_dir = home / "hermes-plus"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "dashboard.log"
        command = [
            self.inst.hermes_bin, "dashboard", "--host", "127.0.0.1", "--port", str(self.port),
            "--no-open", "--skip-build", "--isolated",
        ]
        with self.log_path.open("ab") as output:
            options: dict = {
                "stdout": output,
                "stderr": subprocess.STDOUT,
                "env": self._environment(),
            }
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            self.process = subprocess.Popen(command, **options)
        self.url = f"http://127.0.0.1:{self.port}"
        self.ws_url = f"ws://127.0.0.1:{self.port}/api/ws"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.stop()
                raise RuntimeError(
                    f"Hermes dashboard exited with code {self.process.returncode}; log: {self.log_path}"
                )
            try:
                with urllib.request.urlopen(f"{self.url}/api/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        self.stop()
        raise TimeoutError(f"Hermes dashboard did not become ready; log: {self.log_path}")

    def stop(self) -> None:
        """Terminate the dashboard process group, escalating if it does not exit."""
        process = self.process
        if process is None:
            return
        try:
            if os.name == "nt":
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
            else:
                leader_alive = process.poll() is None
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    if leader_alive:
                        process.wait(timeout=10)
                    return
                if process.poll() is None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.wait(timeout=10)
        finally:
            self.process = process
