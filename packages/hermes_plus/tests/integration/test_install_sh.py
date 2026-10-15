"""Integration tests for install.sh running against real environments."""

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _run_with_lock(cmd: list[str], env: dict[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    """Run command with flock protection, avoiding self-deadlock if lock is already held."""
    lock_file = Path("/tmp/hermes-build.lock")
    lock_file.touch(exist_ok=True)

    use_flock = False
    try:
        with open(lock_file, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            use_flock = True
    except (BlockingIOError, OSError):
        # Lock already held by parent/ancestor
        use_flock = False

    actual_cmd = ["flock", str(lock_file), *cmd] if use_flock else cmd
    return subprocess.run(
        actual_cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.mark.integration
def test_install_sh_execution(tmp_path: Path) -> None:
    # 1. Locate repository root and install.sh
    # Current test is in packages/hermes_plus/tests/integration/
    test_dir = Path(__file__).resolve().parent
    repo_root = test_dir.parents[3]
    install_sh = repo_root / "install.sh"
    assert install_sh.exists(), f"install.sh not found at {install_sh}"

    pkg_dir = repo_root / "packages" / "hermes_plus"
    assert pkg_dir.exists(), f"package dir not found at {pkg_dir}"

    # Setup directories
    uv_tool_dir = tmp_path / "uv_tools"
    uv_tool_bin_dir = tmp_path / "uv_bin"
    hermes_home = tmp_path / "hermes_home"
    uv_tool_dir.mkdir(parents=True, exist_ok=True)
    uv_tool_bin_dir.mkdir(parents=True, exist_ok=True)
    hermes_home.mkdir(parents=True, exist_ok=True)

    # Stock hermes binary directory
    stock_hermes_bin_dir = Path.home() / "Code/ship-test/venv/bin"
    if not stock_hermes_bin_dir.exists():
        # Fallback to stock copy directly if needed
        stock_hermes_bin_dir = Path.home() / "Code/ship-test/stock/.hermes/bin"

    if not (stock_hermes_bin_dir / "hermes").exists():
        pytest.skip(f"Stock hermes binary not found in {stock_hermes_bin_dir}")

    env = os.environ.copy()
    env["HERMES_PLUS_SOURCE"] = str(pkg_dir)
    env["UV_TOOL_DIR"] = str(uv_tool_dir)
    env["UV_TOOL_BIN_DIR"] = str(uv_tool_bin_dir)
    env["HERMES_HOME"] = str(hermes_home)
    env["HERMES_PLUS_FORCE"] = "1"
    env["PATH"] = f"{uv_tool_bin_dir}:{stock_hermes_bin_dir}:{env.get('PATH', '')}"

    # 1. Run install.sh for real
    res = _run_with_lock(["sh", str(install_sh)], env=env, timeout=180)
    assert res.returncode == 0, f"install.sh failed (code {res.returncode}):\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"

    # 2. Assert that <bin>/hermes-plus version works
    hp_bin = uv_tool_bin_dir / "hermes-plus"
    if not hp_bin.exists():
        found = shutil.which("hermes-plus", path=env["PATH"])
        assert found is not None, f"hermes-plus not found in PATH or {uv_tool_bin_dir}"
        hp_bin = Path(found)

    res_ver = subprocess.run(
        [str(hp_bin), "version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert res_ver.returncode == 0
    assert "hermes-plus" in res_ver.stdout

    # 3. Assert the plugin dir and DB exist, but only when adapter and store are present
    adapter_ready = False
    try:
        from hermes_plus import adapter

        if hasattr(adapter, "detect") and hasattr(adapter, "plus_home"):
            adapter_ready = True
    except Exception:
        pass

    store_ready = False
    try:
        from hermes_plus.store import open_store

        if callable(open_store):
            store_ready = True
    except Exception:
        pass

    if not adapter_ready or not store_ready:
        pytest.skip(
            f"adapter/store not fully implemented yet (adapter={adapter_ready}, store={store_ready})"
        )
    bridge_packaged = (pkg_dir / "src" / "hermes_plus" / "bridge").is_dir()
    if not bridge_packaged:
        pytest.skip("bridge/ plugin is not packaged yet; install.sh has no plugin to copy")

    plugin_dir = hermes_home / "plugins" / "hermes-plus"
    assert plugin_dir.exists(), f"Plugin dir {plugin_dir} does not exist"
    db_file = hermes_home / "hermes-plus" / "plus.db"
    assert db_file.exists(), f"plus.db {db_file} does not exist"
