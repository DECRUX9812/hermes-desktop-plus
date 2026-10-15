"""Stock profile lifecycle: profiles and their config are changed through the `hermes` CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

from hermes_plus.adapter.cli import run_hermes
from hermes_plus.adapter.paths import hermes_home, list_profiles


class ProfileError(Exception):
    pass


def exists(name: str) -> bool:
    return name in list_profiles()


def create(name: str, *, description: str) -> Path:
    _check(run_hermes(["profile", "create", name, "--clone", "--no-alias", "--description", description]))
    return hermes_home(name)


def delete(name: str) -> None:
    """Delete a profile, tolerating upstream's "settlement pending" exit.

    Stock Hermes tombstones the profile directory, then settles its durable identity. When the
    profile has its own ``state.db`` the settlement is refused (the tombstone makes its home
    unopenable) and the CLI exits non-zero with a retry hint, even though the directory is
    already gone. Measured identical on 379cf122 and v0.22.2. The directory is the contract:
    when it is no longer listed, the delete happened.
    """
    result = run_hermes(["profile", "delete", name, "--yes"])
    if result.returncode == 0 or not exists(name):
        return
    _check(result)


def config_set(name: str, key: str, value: str) -> None:
    _check(run_hermes(["config", "set", key, value], profile=name))


def write_soul(name: str, text: str) -> None:
    (hermes_home(name) / "SOUL.md").write_text(text, encoding="utf-8")


def _check(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise ProfileError((result.stderr or result.stdout).strip() or "hermes profile command failed")
