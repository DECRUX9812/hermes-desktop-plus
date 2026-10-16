"""A cold HERMES_HOME must not look like a hang.

Stock prints its provisioning steps on stderr while it prepares the isolated runtime and
the dependency environment. The install relays those lines, so the minutes a first install
takes are visible. Real stock binary, real cold home, no mocks: the enable call usually
outlives the short budget below, and finishes inside it on a warm cache.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_resolve import resolve_stock_hermes

pytestmark = pytest.mark.integration

BRIDGE_SRC = Path(__file__).resolve().parents[2] / "src" / "hermes_plus" / "bridge"
# Long enough for stock to reach its first progress line, short enough to stop the sync.
BUDGET_SECONDS = "15"


def test_a_cold_enable_relays_stock_progress(tmp_path: Path) -> None:
    binary, _ = resolve_stock_hermes()
    if not os.access(binary, os.X_OK):
        pytest.skip(f"stock Hermes not executable at {binary}")
    runtime_dir = os.environ.get("HERMES_RUNTIME_DIR")
    if not runtime_dir or not any(
        child.name.startswith("python") for child in Path(runtime_dir).glob("*")
    ):
        # A cold tool store spends the whole budget downloading uv and Python before
        # stock says anything, so the relay would have nothing to show yet.
        pytest.skip(f"stock tool store at {runtime_dir!r} is not warm")
    home = tmp_path / "cold-home"
    # `plugins enable` resolves the name against the home's plugins dir first.
    shutil.copytree(BRIDGE_SRC, home / "plugins" / "hermes-plus")
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        # Keep every write inside tmp_path: HOME decides where caches and the default
        # HERMES_HOME would land.
        "HOME": str(tmp_path),
        "HERMES_HOME": str(home),
        "HERMES_RUNTIME_DIR": runtime_dir,
        "HERMES_PLUS_INSTALL_TIMEOUT": BUDGET_SECONDS,
    }
    result = subprocess.run(
        [sys.executable, "-m", "hermes_plus.cli", "install", "--hermes", str(binary)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    # With a warm uv cache the whole sync can finish inside the budget, so either exit is
    # legitimate; what must hold either way is that stock's progress was relayed.
    assert result.returncode in (0, 1), result.stdout + result.stderr
    assert "may be preparing its runtime" in result.stdout
    relayed = result.stderr
    assert (
        "Preparing the isolated Hermes runtime" in relayed
        or "Installing Python dependencies" in relayed
    ), f"stock's progress never reached the user:\n{relayed[-2000:]}"
    if result.returncode == 1:
        assert "did not finish within 15 s" in relayed
