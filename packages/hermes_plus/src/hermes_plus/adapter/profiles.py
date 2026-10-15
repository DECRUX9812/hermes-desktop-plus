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
    _check(run_hermes(["profile", "delete", name, "--yes"]))


def config_set(name: str, key: str, value: str) -> None:
    _check(run_hermes(["config", "set", key, value], profile=name))


def write_soul(name: str, text: str) -> None:
    (hermes_home(name) / "SOUL.md").write_text(text, encoding="utf-8")


def _check(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise ProfileError((result.stderr or result.stdout).strip() or "hermes profile command failed")
