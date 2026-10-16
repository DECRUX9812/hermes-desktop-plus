"""Sharing a provisioned home's dependency state with fresh test homes, for real.

Stock Hermes scopes dependency state per data root (``<home>/installs/<key>``) and builds a
whole environment there on the first ``plugins enable`` in a new home (measured: 387-645s).
``HERMES_PLUS_TEST_SHARED_INSTALLS`` makes a fresh home borrow a provisioned home's
``installs/``, so that sync finds the provisioned state and is a no-op.

These tests run the real stock binary and the real ``hermes-plus install`` -- no mocks.
They skip unless the variable is set: without a provisioned home there is nothing to borrow.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from hermes_resolve import resolve_stock_hermes
from support import SHARED_INSTALLS_ENV, share_installs

pytestmark = pytest.mark.integration


def _shared_home() -> Path:
    raw = os.environ.get(SHARED_INSTALLS_ENV, "").strip()
    if not raw:
        pytest.skip(f"{SHARED_INSTALLS_ENV} is unset; nothing to share with a fresh home")
    return Path(raw).expanduser()


def _generations(installs: Path) -> set[Path]:
    return {path for path in installs.glob("*/environments/*") if path.is_dir()}


def _receipt(home: Path) -> dict:
    path = home / "logs" / "update_receipts" / "latest.json"
    assert path.is_file(), f"stock Hermes wrote no receipt at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _install_env(home: Path, stock_hermes: str) -> dict[str, str]:
    return {**os.environ, "HERMES_HOME": str(home), "HERMES_PLUS_HERMES_BIN": stock_hermes}


def _assert_no_op(home: Path, elapsed: float) -> None:
    rebuild = _receipt(home)["venv_rebuild"]
    assert rebuild["ok"] is False and rebuild["reason"] == "already in sync", (
        f"stock rebuilt the dependency environment instead of reusing the shared one "
        f"({rebuild}) in {elapsed:.1f}s -- the shared home is not the one this binary "
        f"({resolve_stock_hermes()[0]}) was provisioned with"
    )


def test_shared_installs_turns_the_first_sync_into_a_no_op(stock_hermes: str, plus_bin: str,
                                                           tmp_path: Path) -> None:
    source = _shared_home()
    home = tmp_path / "home"
    installs = share_installs(home)
    assert installs is not None and (home / "installs").is_symlink()
    before = _generations(installs)

    started = time.monotonic()
    result = subprocess.run(
        [plus_bin, "install", "--hermes", stock_hermes, "--extras", "backdrops"],
        env=_install_env(home, stock_hermes), capture_output=True, text=True, timeout=900)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    _assert_no_op(home, elapsed)
    assert _generations(installs) == before, "a sync built a new generation in the shared state"
    # The install is real: the bridge landed in this home's own plugin dir.
    assert (home / "plugins" / "hermes-plus" / "plugin.yaml").is_file()


def test_two_homes_share_one_install_state_at_once(stock_hermes: str, plus_bin: str,
                                                   tmp_path: Path) -> None:
    """Two fresh homes enabling plugins concurrently on one shared state.

    Stock serializes them itself: ``sync_venv`` runs under an exclusive ``flock`` on
    ``<state>/.install.lock`` (``pm/install.py::_venv_install_lock`` -> ``pm/runtime_state.py::
    runtime_lock``), and an explicit install waits for it (``timeout=None``) instead of
    skipping, so the loser sees the state the winner committed. No extra lock is needed.
    """
    source = _shared_home()
    homes = [tmp_path / "home-a", tmp_path / "home-b"]
    for home in homes:
        assert share_installs(home) is not None
    installs = source / "installs"
    before = _generations(installs)

    started = time.monotonic()
    processes = [
        subprocess.Popen(
            [plus_bin, "install", "--hermes", stock_hermes],
            env=_install_env(home, stock_hermes), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        for home in homes
    ]
    outputs = [(process.wait(timeout=900), process) for process in processes]
    elapsed = time.monotonic() - started

    for code, process in outputs:
        assert code == 0, f"exit {code}\n{process.stdout.read()}\n{process.stderr.read()}"
    for home in homes:
        _assert_no_op(home, elapsed)
    assert _generations(installs) == before, "a concurrent sync built a new generation"
