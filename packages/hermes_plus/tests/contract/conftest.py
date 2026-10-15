from __future__ import annotations

import os
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


@pytest.fixture(scope="session")
def dashboard(stock_hermes: str, stock_install, tmp_path_factory):
    home = tmp_path_factory.mktemp("hermes-plus-contract-home")
    with _environment(HERMES_HOME=str(home), HERMES_PLUS_HERMES_BIN=stock_hermes):
        configured = run_hermes(["config", "set", "display.compact", "true"], timeout=120)
    if configured.returncode:
        if "No module named" in configured.stderr and "ruamel" in configured.stderr:
            pytest.skip("stock Hermes runtime is incomplete: its installed environment lacks ruamel")
        pytest.fail(
            "stock Hermes could not initialize the contract home with config set: "
            f"{configured.stderr.strip() or configured.stdout.strip()}"
        )
    inst = replace(stock_install, hermes_home=home)
    process = DashboardProcess(inst, port=0, extra_env={"HERMES_HOME": str(home)})
    process.start(timeout=120)
    try:
        yield process, home
    finally:
        child = process.process
        pid = child.pid if child is not None else None
        process.stop()
        if child is not None:
            assert child.poll() is not None, "stock dashboard process was not reaped"
        if pid is not None and os.name != "nt":
            with pytest.raises(ProcessLookupError):
                os.killpg(pid, 0)
