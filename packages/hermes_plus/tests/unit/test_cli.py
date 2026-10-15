"""Tests for hermes-plus CLI running as real subprocesses (zero mocks)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from hermes_resolve import resolve_stock_hermes


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


def _stock_hermes() -> str | None:
    """The stock launcher under test: HERMES_PLUS_TEST_HERMES, then `hermes` on PATH."""
    binary, _ = resolve_stock_hermes()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return None
    return str(binary)


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


def test_cli_doctor_json(tmp_path: Path) -> None:
    # Hermetic: doctor must inspect the stock install under test, never
    # whatever `hermes` the ambient PATH happens to expose (it may be another
    # install, or hang repairing an unrelated one). Pinning
    # HERMES_PLUS_HERMES_BIN takes precedence over PATH resolution.
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "hermes-home")
    env.pop("HERMES_PLUS_HERMES_BIN", None)
    stock = _stock_hermes()
    if stock:
        env["HERMES_PLUS_HERMES_BIN"] = stock
    res = _run_cli("doctor", "--json", env=env)
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
    # Use a local source to avoid network calls during testing.
    res = _run_cli("update", "--check", "--source", "/tmp/hermes-plus.whl")
    assert res.returncode == 0
    assert "Upgrade Check:" in res.stdout
    assert "Upgrade command:" in res.stdout
    assert "/tmp/hermes-plus.whl" in res.stdout
    assert "Re-install command:" in res.stdout


def test_install_sh_version_matches_package_version() -> None:
    """The version in install.sh must match pyproject.toml and plugin.yaml."""
    import re
    import yaml

    repo_root = Path(__file__).resolve().parents[4]
    install_sh = repo_root / "install.sh"
    pyproject_toml = repo_root / "packages" / "hermes_plus" / "pyproject.toml"
    plugin_yaml = repo_root / "packages" / "hermes_plus" / "src" / "hermes_plus" / "bridge" / "plugin.yaml"

    # Extract version from install.sh
    install_sh_content = install_sh.read_text()
    match = re.search(r'HERMES_PLUS_VERSION="\$\{HERMES_PLUS_VERSION:-([^}]+)\}', install_sh_content)
    assert match, "Could not find HERMES_PLUS_VERSION default in install.sh"
    install_sh_version = match.group(1)

    # Extract version from pyproject.toml
    pyproject_content = pyproject_toml.read_text()
    match = re.search(r'version\s*=\s*"([^"]+)"', pyproject_content)
    assert match, "Could not find version in pyproject.toml"
    pyproject_version = match.group(1)

    # Extract version from plugin.yaml
    plugin_data = yaml.safe_load(plugin_yaml.read_text())
    plugin_version = plugin_data.get("version")
    assert plugin_version, "Could not find version in plugin.yaml"

    assert install_sh_version == pyproject_version, (
        f"install.sh version ({install_sh_version}) does not match pyproject.toml ({pyproject_version})"
    )
    assert install_sh_version == plugin_version, (
        f"install.sh version ({install_sh_version}) does not match plugin.yaml ({plugin_version})"
    )


def test_cli_update_github_url_construction() -> None:
    """Test that _build_wheel_url constructs correct GitHub release wheel URLs."""
    from hermes_plus.cli import _build_wheel_url

    url = _build_wheel_url("DECRUX9812/hermes-desktop-plus", "v0.1.0", "0.1.0")
    expected = (
        "https://github.com/DECRUX9812/hermes-desktop-plus/releases/download/"
        "v0.1.0/hermes_plus-0.1.0-py3-none-any.whl"
    )
    assert url == expected


def test_cli_update_github_release_offline_error() -> None:
    """Test that _resolve_github_release_tag handles offline errors gracefully."""
    from hermes_plus.cli import _resolve_github_release_tag

    # Point at an unroutable host; should raise an exception with a clear message.
    try:
        _resolve_github_release_tag("owner/repo", timeout=1)
        assert False, "Expected _resolve_github_release_tag to raise an exception"
    except Exception as exc:
        assert "Could not fetch latest release" in str(exc)
        assert "owner/repo" in str(exc)


def test_cli_update_with_source_override() -> None:
    """Test that update --source works with a local source override."""
    res = _run_cli("update", "--check", "--source", "/tmp/local-wheel.whl")
    assert res.returncode == 0
    assert "/tmp/local-wheel.whl" in res.stdout


def test_extract_version_from_tag() -> None:
    """Test that _extract_version_from_tag correctly strips 'v' prefix."""
    from hermes_plus.cli import _extract_version_from_tag

    assert _extract_version_from_tag("v0.1.0") == "0.1.0"
    assert _extract_version_from_tag("v0.2.0") == "0.2.0"
    assert _extract_version_from_tag("0.1.0") == "0.1.0"
    assert _extract_version_from_tag("v1.2.3-rc1") == "1.2.3-rc1"


def test_cli_update_version_derived_from_tag() -> None:
    """Test that tag→version→URL mapping works for tags other than current version.

    This verifies the fix for the bug where the hardcoded version did not
    match the tag, resulting in incorrect wheel URLs.
    """
    from hermes_plus.cli import _build_wheel_url, _extract_version_from_tag

    # Simulate fetching a newer release (v0.2.0)
    tag = "v0.2.0"
    version = _extract_version_from_tag(tag)
    assert version == "0.2.0", "Version extraction failed"

    url = _build_wheel_url("DECRUX9812/hermes-desktop-plus", tag, version)
    expected = (
        "https://github.com/DECRUX9812/hermes-desktop-plus/releases/download/"
        "v0.2.0/hermes_plus-0.2.0-py3-none-any.whl"
    )
    assert url == expected, f"URL mismatch: expected {expected}, got {url}"


def test_wheel_build_filename_matches_version() -> None:
    """Verify that the wheel filename matches the version in pyproject.toml.

    This test builds the actual wheel and checks that the filename matches
    what _build_wheel_url expects.
    """
    import subprocess
    import tempfile
    import re

    repo_root = Path(__file__).resolve().parents[4]
    pyproject_toml = repo_root / "packages" / "hermes_plus" / "pyproject.toml"

    # Extract version from pyproject.toml
    pyproject_content = pyproject_toml.read_text()
    match = re.search(r'version\s*=\s*"([^"]+)"', pyproject_content)
    assert match, "Could not find version in pyproject.toml"
    expected_version = match.group(1)

    # Build the wheel
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["uv", "build", "--wheel", "packages/hermes_plus", "--out-dir", tmpdir],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"wheel build failed: {result.stderr}"

        # Find the built wheel file
        wheel_files = list(Path(tmpdir).glob("hermes_plus-*.whl"))
        assert len(wheel_files) == 1, f"Expected exactly one wheel file, found {len(wheel_files)}"

        wheel_filename = wheel_files[0].name
        # Extract version from wheel filename (e.g., hermes_plus-0.1.0-py3-none-any.whl)
        match = re.search(r"hermes_plus-([^-]+)-py3-none-any\.whl", wheel_filename)
        assert match, f"Could not parse version from wheel filename: {wheel_filename}"
        wheel_version = match.group(1)

        assert (
            wheel_version == expected_version
        ), f"Wheel version ({wheel_version}) does not match pyproject.toml ({expected_version})"
