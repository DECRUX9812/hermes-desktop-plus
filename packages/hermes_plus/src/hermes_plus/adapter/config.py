"""Read-only Hermes config access and plugin enablement."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from hermes_plus.adapter.cli import run_hermes
from hermes_plus.adapter.paths import hermes_home
from hermes_plus.adapter.version import resolve_hermes_bin


def load_config(profile: str | None = None) -> dict[str, Any]:
    """Load config.yaml without merging defaults or writing to the profile."""
    path = hermes_home(profile) / "config.yaml"
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Hermes config must contain a YAML mapping: {path}")
    return value


def _atomic_config_write(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            yaml.safe_dump(config, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def enable_plugin(name: str, profile: str | None = None, *, timeout: int = 300) -> None:
    """Enable a plugin through Hermes, using an atomic config fallback only without its CLI."""
    if not name.strip():
        raise ValueError("plugin name cannot be empty")
    if resolve_hermes_bin() is not None:
        result = run_hermes(["plugins", "enable", name], profile=profile, timeout=timeout)
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, result.args, output=result.stdout, stderr=result.stderr
            )
        return

    path = hermes_home(profile) / "config.yaml"
    config = load_config(profile)
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("Hermes config 'plugins' section must be a mapping")
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        raise ValueError("Hermes config 'plugins.enabled' value must be a list")
    if name not in enabled:
        enabled.append(name)
    _atomic_config_write(path, config)
