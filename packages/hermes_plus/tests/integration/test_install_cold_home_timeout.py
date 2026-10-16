"""`hermes-plus install` in a cold HERMES_HOME with a 1 s budget, against the real stock Hermes.

The stock CLI provisions its runtime on the first real command in a fresh home, which takes
minutes, so the first `plugins enable` really exceeds the budget. Zero mocks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_resolve import resolve_stock_hermes

pytestmark = pytest.mark.integration


def test_cold_home_budget_overrun_is_a_clean_error(tmp_path: Path) -> None:
    binary, _ = resolve_stock_hermes()
    if not os.access(binary, os.X_OK):
        pytest.skip(f"stock Hermes not executable at {binary}")
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "HERMES_HOME": str(tmp_path / "cold-home"),
        "HERMES_PLUS_INSTALL_TIMEOUT": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "hermes_plus.cli", "install", "--hermes", str(binary)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "may be preparing its runtime" in result.stdout
    assert "'hermes plugins enable hermes-plus' did not finish within 1 s" in result.stderr
    assert "Re-run 'hermes-plus install'" in result.stderr
    assert "HERMES_PLUS_INSTALL_TIMEOUT=<seconds>" in result.stderr
