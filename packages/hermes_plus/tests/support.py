from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Test-only opt-in, read by every fixture that creates a fresh HERMES_HOME and then
# runs a dependency operation. See CONTRIBUTING.md, "Getting a stock Hermes for tests".
SHARED_INSTALLS_ENV = "HERMES_PLUS_TEST_SHARED_INSTALLS"


def share_installs(home: Path) -> Path | None:
    """Borrow a provisioned home's ``installs/`` for a fresh test home (opt-in, test-only).

    Stock Hermes scopes dependency state per data root (``<home>/installs/<key>``,
    ``pm/environments.py::install_state_dir``) and builds a whole environment there on the
    first ``plugins enable`` in a new home (measured: 387-645s). When
    ``HERMES_PLUS_TEST_SHARED_INSTALLS`` names a provisioned home, ``<home>/installs``
    becomes a symlink to that home's, so the sync finds the provisioned state and is a
    no-op ("already in sync", ~0.1s).

    The named home must be the one the stock binary under test was provisioned with: the
    key is ``sha256(<the binary's Install directory>)[:16]``, so a home provisioned for a
    different checkout shares nothing and the sync rebuilds. Unset, nothing is created and
    behaviour is unchanged. Returns the borrowed ``installs/`` path, or ``None``.
    """
    raw = os.environ.get(SHARED_INSTALLS_ENV, "").strip()
    if not raw:
        return None
    source = Path(raw).expanduser()
    if not source.is_dir():
        pytest.fail(f"{SHARED_INSTALLS_ENV}={raw} is not a directory; name a provisioned HERMES_HOME")
    installs = source / "installs"
    if not installs.is_dir():
        pytest.fail(f"{SHARED_INSTALLS_ENV}={raw} has no installs/ directory; name a provisioned HERMES_HOME")
    home.mkdir(parents=True, exist_ok=True)
    target = home / "installs"
    if target.is_symlink():
        if target.resolve() != installs.resolve():
            pytest.fail(f"{home}/installs already points at {target.resolve()}, not {installs}")
        return installs
    if target.exists():
        pytest.fail(f"{home} already has its own installs/; share_installs needs a fresh home")
    target.symlink_to(installs)
    return installs


def ok(reply: dict[str, Any]) -> Any:
    assert "error" not in reply, reply
    return reply["result"]


def fault(reply: dict[str, Any]) -> dict[str, Any]:
    assert "result" not in reply, reply
    return reply["error"]
