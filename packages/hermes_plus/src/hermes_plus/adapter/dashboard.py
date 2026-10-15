"""Lifecycle management for a stock Hermes dashboard child."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from hermes_plus.adapter.paths import hermes_home
from hermes_plus.adapter.version import HermesInstall

log = logging.getLogger(__name__)

STUB_INDEX_HTML = "<!doctype html><html><body>hermes-plus stub</body></html>"


class DashboardProcess:
    """Start the stock dashboard on loopback and manage its process group.

    The child's HTTP surface is only used for ``/api/health`` readiness and the
    ``/api/ws`` gateway: ``hermes-plus serve`` serves its own UI from the
    ``packages/ui`` build and never uses the upstream web dist.
    """

    def __init__(
        self,
        inst: HermesInstall,
        *,
        port: int = 0,
        token: str | None = None,
        profile: str | None = None,
        web_dist: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.inst = inst
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self.profile = profile
        self.web_dist = Path(web_dist).expanduser() if web_dist is not None else None
        self.extra_env = dict(extra_env or {})
        self.process: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self.url = ""
        self.ws_url = ""
        self._stub_dist_dir: Path | None = None

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

    def _upstream_dist_root(self) -> Path | None:
        """Ask the install's interpreter where upstream's packaged web dist lives."""
        python = self.inst.python
        if not python:
            return None
        try:
            result = subprocess.run(
                [python, "-c",
                 "import pathlib, hermes_cli; print(pathlib.Path(hermes_cli.__file__).parent / 'web_dist')"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        lines = result.stdout.strip().splitlines()
        return Path(lines[-1].strip()) if result.returncode == 0 and lines else None

    def _ensure_web_dist(self, env: dict[str, str]) -> None:
        """Guarantee a servable dist, because upstream ``--skip-build`` aborts otherwise.

        Upstream aborts when the dist root has no ``index.html`` unless a recovery
        build is possible; a ``--runtime-only`` install has no dist at all, so we
        point ``HERMES_WEB_DIST`` at a stub created in the process temp area.
        """
        default_dist = self._upstream_dist_root()
        if default_dist is not None and (default_dist / "index.html").is_file():
            return
        if self._stub_dist_dir is None:
            self._stub_dist_dir = Path(tempfile.mkdtemp(prefix="hermes-plus-webdist-"))
            (self._stub_dist_dir / "index.html").write_text(STUB_INDEX_HTML, encoding="utf-8")
            log.warning(
                "The stock Hermes install has no web UI dist; starting the dashboard "
                "child with a stub dist at %s (hermes-plus serve never uses the upstream dist).",
                self._stub_dist_dir,
            )
        env["HERMES_WEB_DIST"] = str(self._stub_dist_dir)

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("HERMES_WEB_DIST", None)
        env.update(self.extra_env)
        home = self._home()
        env["HERMES_HOME"] = str(home)
        env["HERMES_DASHBOARD_SESSION_TOKEN"] = self.token
        if self.web_dist is not None:
            env["HERMES_WEB_DIST"] = str(self.web_dist)
        elif "HERMES_WEB_DIST" not in env:
            self._ensure_web_dist(env)
        return env

    def start(self, timeout: float = 120) -> None:
        """Start dashboard and wait for the public liveness endpoint.

        A cold HERMES_HOME pays a runtime prepare before the child serves,
        so the default must exceed that, not just the import time.
        """
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
        # --isolated makes a named-profile launch run this dedicated server instead
        # of routing to (or starting) the machine-level server, which would be the
        # user's real one outside our HERMES_HOME.
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
        if "HERMES_WEB_DIST" in options["env"]:
            self.web_dist = Path(options["env"]["HERMES_WEB_DIST"])
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
            if self._stub_dist_dir is not None:
                shutil.rmtree(self._stub_dist_dir, ignore_errors=True)
                self._stub_dist_dir = None
