"""One resolver for the stock Hermes binary under test.

``HERMES_PLUS_TEST_HERMES`` wins; otherwise ``hermes`` on PATH is the CI
fallback. Every fixture and test resolves the binary through this module, so a
session always tests one binary and the pytest header names which one ran.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ENV_VAR = "HERMES_PLUS_TEST_HERMES"


def resolve_stock_hermes() -> tuple[Path, str]:
    """Return ``(binary, source)``; ``source`` says how it was found, for reporting."""
    configured = os.environ.get(ENV_VAR)
    if configured:
        return Path(configured).expanduser(), f"from {ENV_VAR}"
    found = shutil.which("hermes")
    if found:
        return Path(found), "from PATH fallback"
    return Path("hermes"), "NOT RESOLVED (no HERMES_PLUS_TEST_HERMES, no `hermes` on PATH)"


def prepend_bin_dir(env: dict[str, str]) -> dict[str, str]:
    """Make every child process resolve the same ``hermes`` we were configured with.

    Stock code and our own teardown may spawn ``hermes`` by name; without this,
    whatever launcher sits earlier on PATH (often a personal install) answers
    instead of the configured binary.
    """
    binary, _ = resolve_stock_hermes()
    if binary.is_file():
        env["PATH"] = os.pathsep.join([str(binary.resolve().parent), env.get("PATH", os.defpath)])
    return env
