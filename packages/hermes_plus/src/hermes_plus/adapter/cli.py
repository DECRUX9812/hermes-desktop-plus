"""Hermes CLI subprocess boundary."""

from __future__ import annotations

import os
import subprocess

from hermes_plus.adapter.paths import hermes_home
from hermes_plus.adapter.version import resolve_hermes_bin


def run_hermes(
    args: list[str], *, profile: str | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    """Run the installed Hermes CLI, binding an optional profile through HERMES_HOME."""
    binary = resolve_hermes_bin()
    if binary is None:
        raise FileNotFoundError(
            "Hermes executable not found; set HERMES_PLUS_HERMES_BIN or add hermes to PATH"
        )
    env = os.environ.copy()
    if profile is not None:
        env["HERMES_HOME"] = str(hermes_home(profile))
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
