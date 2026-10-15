"""Tests for hermes-plus CLI running as real subprocesses (zero mocks)."""

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Execute hermes-plus CLI via python -m hermes_plus.cli."""
    cmd = [sys.executable, "-m", "hermes_plus.cli", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_cli_help() -> None:
    res = _run_cli("--help")
    assert res.returncode == 0
    assert "hermes-plus" in res.stdout
    assert "version" in res.stdout
    assert "install" in res.stdout
    assert "serve" in res.stdout
    assert "doctor" in res.stdout
    assert "update" in res.stdout
    assert "uninstall" in res.stdout


def test_cli_version() -> None:
    res = _run_cli("version")
    assert res.returncode == 0
    assert "hermes-plus" in res.stdout
    assert "adapter API" in res.stdout
    assert "hermes" in res.stdout


def test_cli_version_json() -> None:
    res = _run_cli("version", "--json")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "hermes_plus" in data
    assert "adapter_api" in data
    assert "hermes" in data


def test_cli_serve_auth_none_external_refused() -> None:
    # serve --auth none --host 0.0.0.0 must be refused
    res = _run_cli("serve", "--auth", "none", "--host", "0.0.0.0")
    assert res.returncode != 0
    assert "Refusing auth='none' on non-loopback host" in res.stderr


def test_cli_doctor_json() -> None:
    res = _run_cli("doctor", "--json")
    # Doctor should output valid JSON regardless of exit code (0 or 1)
    assert res.returncode in (0, 1)
    data = json.loads(res.stdout)
    assert isinstance(data, list)
    names = [c["name"] for c in data]
    assert "hermes_binary" in names
    assert "hermes_compat" in names
    assert "hermes_home" in names
    assert "bridge_plugin" in names
    assert "plus_db" in names
    assert "ui_dist" in names
    assert "default_port" in names
    assert "free_disk" in names


def test_cli_update_check() -> None:
    res = _run_cli("update", "--check")
    assert res.returncode == 0
    assert "Upgrade Check:" in res.stdout
    assert "Upgrade command:" in res.stdout
    assert "Re-install command:" in res.stdout
