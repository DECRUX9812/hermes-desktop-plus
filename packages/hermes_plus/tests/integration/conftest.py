"""Shared fixtures for the bridge integration tests: a real stock Hermes and a real install."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PLUS_BIN = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "hermes-plus"


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
def plus_bin() -> str:
    if not PLUS_BIN.is_file():
        pytest.skip(f"hermes-plus CLI is not built into the worktree venv: {PLUS_BIN}")
    return str(PLUS_BIN)


@pytest.fixture(scope="session")
def installed_home(stock_hermes: str, plus_bin: str, tmp_path_factory) -> Path:
    """A temp HERMES_HOME with a real `hermes-plus install` (bridge + backdrops extra)."""
    home = tmp_path_factory.mktemp("hermes-plus-bridge-home")
    subprocess.run(
        [plus_bin, "install", "--hermes", stock_hermes, "--extras", "backdrops"],
        env={**os.environ, "HERMES_HOME": str(home)},
        capture_output=True, text=True, timeout=600, check=True)
    return home
