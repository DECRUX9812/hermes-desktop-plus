from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_plus.adapter import DashboardProcess, GatewayClient, detect, run_hermes
from hermes_plus.rpc import RpcContext
from hermes_plus.rpc.context import GatewayUpstream


# Track all temp HERMES_HOME directories created during the session for cleanup
_temp_homes: list[Path] = []


@contextmanager
def _environment(**values: str):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def stock_hermes() -> str:
    configured = os.environ.get("HERMES_PLUS_TEST_HERMES") or shutil.which("hermes") or "hermes"
    binary = Path(configured).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.skip(f"stock Hermes executable is unavailable: {binary}")
    return str(binary.resolve())


@pytest.fixture(scope="session")
def stock_install(stock_hermes: str):
    return detect(stock_hermes)


@pytest.fixture(scope="session")
def stock_checkout() -> Path:
    """The upstream checkout a pm-managed install was installed from.

    Resolution order: HERMES_PLUS_TEST_HERMES_CHECKOUT, then
    ``$HERMES_HOME/hermes-agent`` (the CI provisioning layout).
    """
    candidates = [os.environ.get("HERMES_PLUS_TEST_HERMES_CHECKOUT")]
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        candidates.append(str(Path(hermes_home) / "hermes-agent"))
    for candidate in candidates:
        path = Path(candidate).expanduser() if candidate else None
        if path is not None and (path / ".git").exists():
            return path
    pytest.skip(
        "no upstream checkout for the stock install "
        "(set HERMES_PLUS_TEST_HERMES_CHECKOUT or HERMES_HOME)"
    )


@pytest.fixture(scope="session")
def expected_compat() -> frozenset[str]:
    """Compat statuses the detected install may report (never unsupported unless listed)."""
    raw = os.environ.get("HERMES_PLUS_TEST_EXPECT_COMPAT", "supported")
    allowed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    unknown = allowed - {"supported", "untested", "unsupported"}
    if not allowed or unknown:
        pytest.fail(
            f"HERMES_PLUS_TEST_EXPECT_COMPAT names unknown statuses: {sorted(unknown)}"
        )
    return frozenset(allowed)


DASHBOARD_PORTS = range(9300, 9400)

# A probed loopback port can be taken before the child binds it (executors share
# 9300-9399). Our own server says "address already in use"; stock Hermes exits
# with BACKEND_PORT_IN_USE when its --port is taken.
PORT_IN_USE_SIGNATURES = ("address already in use", "BACKEND_PORT_IN_USE")


def loopback_port(exclude: set[int] | None = None) -> int:
    skip = exclude or set()
    for port in DASHBOARD_PORTS:
        if port in skip:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("no free loopback port in 9300-9399 for the stock dashboard")


def assert_no_orphaned_processes(home: Path) -> None:
    """Assert that no live processes are using the given HERMES_HOME.

    Uses pgrep to check for processes with the exact home path in their environment.
    """
    if os.name == "nt":
        return  # pgrep not available on Windows

    try:
        result = subprocess.run(
            ["pgrep", "-a", "-f", str(home)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # pgrep found matching processes (exit code 0 means match found)
            pytest.fail(f"orphaned processes still using {home}:\n{result.stdout}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # pgrep timeout or not available


def _stop_gateway_daemons(home: Path) -> None:
    """Stop all gateway daemons associated with the given HERMES_HOME."""
    try:
        # Set HERMES_HOME in environment and run hermes gateway stop
        env = os.environ.copy()
        env["HERMES_HOME"] = str(home)
        subprocess.run(
            ["hermes", "gateway", "stop"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Timeout or hermes not in PATH; will be caught by process check


def start_dashboard(stock_install, home: Path) -> DashboardProcess:
    """Start a real dashboard child with no web dist; the adapter must stub one.

    Concurrent executors share the 9300-9399 range, so a port that probed free can
    be taken before the child binds it; retry that one case and let the rest raise.
    """
    inst = replace(stock_install, hermes_home=home)
    taken: set[int] = set()
    for attempt in range(6):
        process = DashboardProcess(
            inst, port=loopback_port(exclude=taken), extra_env={"HERMES_HOME": str(home)}
        )
        try:
            process.start(timeout=120)
            return process
        except RuntimeError:
            log_text = (
                process.log_path.read_text(errors="replace")[-4000:]
                if process.log_path is not None and process.log_path.exists()
                else ""
            )
            if (
                not any(sig in log_text for sig in PORT_IN_USE_SIGNATURES)
                or attempt + 1 == 6
            ):
                raise
            taken.add(process.port)
    raise RuntimeError("stock dashboard never got a free loopback port")


def stop_dashboard(process: DashboardProcess) -> None:
    child = process.process
    pid = child.pid if child is not None else None
    process.stop()
    if child is not None:
        assert child.poll() is not None, "stock dashboard process was not reaped"
    if pid is not None and os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)


@pytest.fixture
def make_dashboard(stock_install, tmp_path):
    started: list[DashboardProcess] = []

    def start(home: Path) -> DashboardProcess:
        process = start_dashboard(stock_install, home)
        started.append(process)
        return process

    yield start
    for process in started:
        stop_dashboard(process)


@pytest.fixture(scope="session")
def dashboard(stock_hermes: str, stock_install, tmp_path_factory):
    home = tmp_path_factory.mktemp("hermes-plus-contract-home")
    _temp_homes.append(home)

    with _environment(HERMES_HOME=str(home), HERMES_PLUS_HERMES_BIN=stock_hermes):
        configured = run_hermes(["config", "set", "display.compact", "true"], timeout=120)
    if configured.returncode:
        pytest.fail(
            "stock Hermes could not initialize the contract home with config set: "
            f"{configured.stderr.strip() or configured.stdout.strip()}"
        )
    process = start_dashboard(stock_install, home)
    try:
        yield process, home
    finally:
        stop_dashboard(process)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_gateway_daemons(request):
    """Session-scoped finalizer to stop all gateway daemons and verify cleanup."""
    def cleanup():
        for home in _temp_homes:
            _stop_gateway_daemons(home)
            # Give processes a moment to shut down
            import time
            time.sleep(1)
            # Assert no orphaned processes remain
            assert_no_orphaned_processes(home)

    request.addfinalizer(cleanup)


@pytest.fixture
async def ctx(dashboard, store):
    """Override the parent ctx fixture to include upstream from the dashboard."""
    process, _ = dashboard
    client = GatewayClient(process.ws_url, process.token)
    await client.connect()
    context = RpcContext(store, upstream=GatewayUpstream(client))
    yield context
    await client.close()
