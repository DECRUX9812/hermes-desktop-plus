from __future__ import annotations

import json
import os
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_plus.adapter import DashboardProcess, detect, run_hermes


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
    configured = os.environ.get(
        "HERMES_PLUS_TEST_HERMES",
        str(Path.home() / "Code" / "ship-test" / "venv" / "bin" / "hermes"),
    )
    binary = Path(configured).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.skip(f"stock Hermes executable is unavailable: {binary}")
    return str(binary.resolve())


@pytest.fixture(scope="session")
def stock_install(stock_hermes: str):
    return detect(stock_hermes)


DASHBOARD_PORTS = range(9300, 9400)


def loopback_port() -> int:
    for port in DASHBOARD_PORTS:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("no free loopback port in 9300-9399 for the stock dashboard")


def start_dashboard(stock_install, home: Path) -> DashboardProcess:
    """Start a real dashboard child with no web dist; the adapter must stub one."""
    inst = replace(stock_install, hermes_home=home)
    process = DashboardProcess(inst, port=loopback_port(), extra_env={"HERMES_HOME": str(home)})
    process.start(timeout=120)
    return process


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
