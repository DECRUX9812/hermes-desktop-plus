from __future__ import annotations

import asyncio
import os
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


def test_stock_version_and_compat_detection(stock_hermes, stock_install) -> None:
    assert stock_install.hermes_bin == stock_hermes
    assert stock_install.python is not None
    assert stock_install.version is None
    assert stock_install.version_tuple is None
    assert stock_install.upstream_commit == "379cf12"
    assert check_compat(stock_install).status == "untested"
    assert detect(stock_hermes).upstream_commit == stock_install.upstream_commit


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


def test_real_stock_hermes_adapter_contract(stock_hermes, stock_install, dashboard) -> None:
    process, home = dashboard
    with _with_home(home, stock_hermes):
        assert hermes_home() == home
        config = load_config()
        assert config["display"]["compact"] is True
        assert process.process is not None and process.process.poll() is None

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
