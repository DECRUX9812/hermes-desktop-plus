"""Integration tests for install.sh against the real stock Hermes install.

Zero mocks: install.sh is executed for real (uv tool install), the stock CLI is
used for plugin state, and the sidecar's own doctor is parsed from its JSON.
The temp HERMES_HOME borrows the stock checkout's environment: the launcher
binary is the stock one, the home is ours.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
INSTALL_SH = REPO_ROOT / "install.sh"
PACKAGE_DIR = REPO_ROOT / "packages" / "hermes_plus"


def _stock_hermes() -> Path | None:
    """The stock launcher: HERMES_PLUS_TEST_HERMES, then `hermes` on PATH."""
    candidates = [
        os.environ.get("HERMES_PLUS_TEST_HERMES"),
        shutil.which("hermes"),
    ]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def _bridge_packaged() -> bool:
    return importlib.resources.files("hermes_plus").joinpath("bridge", "__init__.py").is_file()


def _install_env(
    tmp_path: Path, hermes_bin: Path, *, source: str | None = None
) -> dict[str, str]:
    """Environment for install.sh: sandboxed tool dirs, temp HERMES_HOME, stock launcher."""
    tool_bin_dir = tmp_path / "uv_bin"
    env = os.environ.copy()
    env.update(
        {
            "HERMES_HOME": str(tmp_path / "hermes_home"),
            "HERMES_PLUS_SOURCE": source if source is not None else str(PACKAGE_DIR),
            "HERMES_PLUS_HERMES_BIN": str(hermes_bin),
            # This machine's stock launcher resolves the version from the ambient
            # home (adapter.detect() drops HERMES_HOME for --version), which can be
            # an older install than the one under test. The compatibility verdict is
            # not what this test checks, so it is forced through.
            "HERMES_PLUS_FORCE": "1",
            "UV_TOOL_DIR": str(tmp_path / "uv_tools"),
            "UV_TOOL_BIN_DIR": str(tool_bin_dir),
            "PATH": os.pathsep.join(
                [str(tool_bin_dir), str(hermes_bin.parent), env.get("PATH", "")]
            ),
        }
    )
    return env


def _run(command: list[str], env: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)


@pytest.mark.integration
def test_install_sh_installs_into_a_real_hermes_home(tmp_path: Path) -> None:
    hermes_bin = _stock_hermes()
    if hermes_bin is None:
        pytest.skip("no stock Hermes launcher found (set HERMES_PLUS_TEST_HERMES)")
    if shutil.which("uv") is None:
        pytest.skip("uv is required for install.sh's tool install path")

    env = _install_env(tmp_path, hermes_bin)
    result = _run(["sh", str(INSTALL_SH)], env)
    assert result.returncode == 0, (
        f"install.sh failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Hermes Desktop Plus is installed." in result.stdout
    # The last step of install.sh is a real doctor run, not a canned message.
    assert "hermes_binary" in result.stdout
    assert "hermes_env" in result.stdout

    hermes_home = Path(env["HERMES_HOME"])
    hp_bin = Path(env["UV_TOOL_BIN_DIR"]) / "hermes-plus"
    assert hp_bin.is_file(), f"hermes-plus was not installed into {hp_bin}"

    version = _run([str(hp_bin), "version", "--json"], env, timeout=60)
    assert version.returncode == 0, version.stderr
    assert json.loads(version.stdout)["hermes_plus"]

    # The store is created and migrated for real (unconditional).
    db_path = hermes_home / "hermes-plus" / "plus.db"
    assert db_path.is_file(), f"{db_path} was not created"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM plus_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row is not None and int(row[0]) >= 1

    if not _bridge_packaged():
        _assert_doctor(env, hp_bin, hermes_bin, hermes_home, expect_bridge_ok=False)
        pytest.skip("hermes_plus.bridge is not packaged yet (S7), so no plugin assertions")

    plugin_dir = hermes_home / "plugins" / "hermes-plus"
    assert (plugin_dir / "plugin.yaml").is_file(), f"bridge plugin missing at {plugin_dir}"

    # The stock CLI must show it enabled in this home's config.yaml.
    listing = _run([str(hermes_bin), "plugins", "list", "--plain", "--no-bundled"], env, timeout=300)
    assert listing.returncode == 0, listing.stderr
    enabled_rows = [
        line for line in listing.stdout.splitlines()
        if "hermes-plus" in line and line.split()[0] == "enabled"
    ]
    assert enabled_rows, f"'hermes plugins list' does not show hermes-plus enabled:\n{listing.stdout}"

    _assert_doctor(env, hp_bin, hermes_bin, hermes_home, expect_bridge_ok=True)


def _assert_doctor(
    env: dict[str, str],
    hp_bin: Path,
    hermes_bin: Path,
    hermes_home: Path,
    *,
    expect_bridge_ok: bool,
) -> None:
    doctor = _run([str(hp_bin), "doctor", "--json", "--hermes", str(hermes_bin)], env, timeout=300)
    checks = {check["name"]: check for check in json.loads(doctor.stdout)}
    assert set(checks) >= {
        "hermes_binary",
        "hermes_compat",
        "hermes_env",
        "hermes_home",
        "bridge_plugin",
        "plus_db",
        "ui_dist",
        "default_port",
        "free_disk",
    }
    assert checks["hermes_binary"]["status"] == "ok", checks["hermes_binary"]
    assert checks["hermes_home"]["status"] == "ok", checks["hermes_home"]
    assert checks["plus_db"]["status"] == "ok", checks["plus_db"]
    assert checks["hermes_env"]["status"] == "ok", checks["hermes_env"]
    assert str(hermes_home) in checks["hermes_env"]["message"]
    if expect_bridge_ok:
        assert checks["bridge_plugin"]["status"] == "ok", checks["bridge_plugin"]


@pytest.mark.integration
def test_install_sh_never_claims_success_when_a_step_fails(tmp_path: Path) -> None:
    hermes_bin = _stock_hermes()
    if hermes_bin is None:
        pytest.skip("no stock Hermes launcher found (set HERMES_PLUS_TEST_HERMES)")
    if shutil.which("uv") is None:
        pytest.skip("uv is required for install.sh's tool install path")

    env = _install_env(tmp_path, hermes_bin, source=str(tmp_path / "not-a-package"))
    result = _run(["sh", str(INSTALL_SH)], env)
    assert result.returncode != 0, result.stdout
    assert "Hermes Desktop Plus is installed." not in result.stdout

    # Same rule for the tool itself: `hermes-plus install` failing (here: an
    # invalid profile name) must be reported and its exit code propagated.
    env = _install_env(tmp_path, hermes_bin)
    env["HERMES_PLUS_PROFILE"] = "Not A Profile"
    result = _run(["sh", str(INSTALL_SH)], env)
    assert result.returncode != 0, result.stdout
    assert "Hermes Desktop Plus is installed." not in result.stdout
    assert "Hermes Desktop Plus is NOT installed." in result.stdout
