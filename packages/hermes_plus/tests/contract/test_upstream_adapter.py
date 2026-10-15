from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

import pytest
from websockets.exceptions import InvalidStatus

from hermes_plus.adapter import (
    GatewayClient,
    RpcError,
    check_compat,
    detect,
    enable_plugin,
    hermes_home,
    load_config,
    probe_capabilities,
    run_hermes,
)
from hermes_plus.adapter.protocol import AUTH_REJECTION_CLOSE_CODE, AUTH_REJECTION_HANDSHAKE_STATUS

pytestmark = pytest.mark.contract


def _expected_upstream_commit(stock_checkout: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(stock_checkout), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, f"the stock checkout is unavailable: {stock_checkout}"
    return result.stdout.strip()


def test_stock_version_and_compat_detection(
    stock_hermes, stock_install, stock_checkout, expected_compat
) -> None:
    assert stock_install.hermes_bin == stock_hermes
    # The probe runs against a fresh temp HERMES_HOME, where this pm-managed
    # install prints the `vunknown` format: no semver, but a recoverable commit.
    assert stock_install.version is None
    assert stock_install.version_tuple is None
    assert stock_install.upstream_commit == _expected_upstream_commit(stock_checkout)
    status = check_compat(stock_install).status
    assert status in expected_compat, (
        f"the pinned install reports compat status {status!r}, "
        f"allowed: {sorted(expected_compat)}"
    )


def test_stock_python_resolution_runs(stock_hermes, stock_install) -> None:
    python = stock_install.python
    assert python is not None
    assert Path(python).is_file()
    result = subprocess.run([python, "--version"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip().startswith("Python ")


def test_detect_reflects_the_caller_environment_not_the_probe_temp_dir(
    stock_hermes, stock_install, tmp_path
) -> None:
    caller_home = tmp_path / "caller-home"
    with _with_home(caller_home, stock_hermes):
        redetected = detect(stock_hermes)
    assert redetected.hermes_home == caller_home
    assert redetected.upstream_commit == stock_install.upstream_commit
    assert redetected.python == stock_install.python
    assert not list(tmp_path.glob("hermes-plus-probe-*"))


@contextmanager
def _with_home(home: Path, hermes_bin: str):
    previous = {key: os.environ.get(key) for key in ("HERMES_HOME", "HERMES_PLUS_HERMES_BIN")}
    os.environ.update({
        "HERMES_HOME": str(home),
        "HERMES_PLUS_HERMES_BIN": hermes_bin,
    })
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_real_stock_hermes_adapter_contract(
    stock_hermes, stock_install, stock_checkout, dashboard
) -> None:
    process, home = dashboard
    with _with_home(home, stock_hermes):
        assert hermes_home() == home
        config = load_config()
        assert config["display"]["compact"] is True
        assert process.process is not None and process.process.poll() is None

    # The adapter had to create a stub dist: this runtime-only install has none.
    assert process.web_dist is not None
    assert (process.web_dist / "index.html").is_file()
    assert stock_checkout not in process.web_dist.parents

    async def exercise_gateway() -> None:
        client = GatewayClient(process.ws_url, process.token)
        await client.connect()
        try:
            assert client.ready is not None
            assert isinstance(client.ready.get("skin"), dict)
            assert client.ready.get("change_events") is True

            session_list = await client.call("session.list", {"limit": 1})
            assert isinstance(session_list, dict)
            assert isinstance(session_list.get("sessions"), list)
            theme = await client.call("config.get", {"key": "theme"})
            assert isinstance(theme, dict)
            assert "value" in theme

            with pytest.raises(RpcError) as unknown:
                await client.call("hermes_plus.contract.unknown")
            assert unknown.value.code == -32601

            capabilities = await probe_capabilities(client)
            assert {"session.list", "config.get"} <= capabilities.methods
            assert capabilities.events
        finally:
            await client.close()

        wrong_token = GatewayClient(process.ws_url, "invalid-contract-token")
        with pytest.raises(InvalidStatus) as rejected:
            await wrong_token.connect()
        # Upstream closes the ASGI socket with 4401 before accept; Uvicorn exposes
        # that pre-upgrade rejection to WebSocket clients as HTTP 403, not a close frame.
        assert AUTH_REJECTION_CLOSE_CODE == 4401
        assert rejected.value.response.status_code == AUTH_REJECTION_HANDSHAKE_STATUS == 403

    asyncio.run(exercise_gateway())

    plugin_dir = home / "plugins" / "hermes-plus-contract-probe"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        "name: hermes-plus-contract-probe\nversion: 0.0.1\ndescription: Contract test plugin\n",
        encoding="utf-8",
    )
    with _with_home(home, stock_hermes):
        result = run_hermes(["config", "path"])
        assert result.returncode == 0
        assert Path(result.stdout.strip()) == home / "config.yaml"
        enable_plugin("hermes-plus-contract-probe")
        assert "hermes-plus-contract-probe" in load_config()["plugins"]["enabled"]

    health = urlopen(f"{process.url}/api/health", timeout=5)
    try:
        assert health.status == 200
    finally:
        health.close()
