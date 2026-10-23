"""Unit tests for the stock dashboard child's spawn environment."""

from __future__ import annotations

from pathlib import Path

from hermes_plus.adapter.dashboard import DashboardProcess
from hermes_plus.adapter.version import HermesInstall


def _install(home: Path) -> HermesInstall:
    """A fake stock install; nothing is launched — only `_environment` runs."""
    return HermesInstall(
        hermes_bin="/opt/demo/hermes/bin/hermes",
        python="/opt/demo/hermes/bin/python",
        version="0.22.1",
        version_tuple=(0, 22, 1),
        upstream_commit=None,
        hermes_home=home,
    )


def _process(home: Path, extra_env: dict[str, str] | None = None) -> DashboardProcess:
    # A web_dist is pinned so `_environment` never probes the fake install for
    # an upstream web dist.
    return DashboardProcess(
        _install(home),
        web_dist=home / "dist",
        extra_env=extra_env,
    )


def test_child_env_pins_the_dashboard_to_loopback(tmp_path) -> None:
    env = _process(tmp_path)._environment()

    # The internal child is loopback-only; hermes-plus is the public face.
    assert env["HERMES_DASHBOARD_PUBLIC_URL"] == "http://127.0.0.1"


def test_child_env_overrides_a_parent_env_public_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "https://dash.example.com")

    env = _process(tmp_path)._environment()

    # A non-loopback public URL would engage stock's auth gate and reject the
    # add-on's token WebSocket with 403.
    assert env["HERMES_DASHBOARD_PUBLIC_URL"] == "http://127.0.0.1"


def test_child_env_overrides_a_config_public_url(tmp_path) -> None:
    # Whatever a config dashboard.public_url passes through extra_env, the
    # child must still be pinned to loopback.
    env = _process(
        tmp_path,
        extra_env={"HERMES_DASHBOARD_PUBLIC_URL": "https://dash.example.com"},
    )._environment()

    assert env["HERMES_DASHBOARD_PUBLIC_URL"] == "http://127.0.0.1"
