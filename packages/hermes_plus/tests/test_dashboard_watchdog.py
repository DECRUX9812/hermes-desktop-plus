"""The sidecar recovers when `hermes update` relaunches its dashboard child without our token."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from hermes_plus.server.runtime import Runtime


def _runtime(poll_result, status_code):
    runtime = object.__new__(Runtime)
    runtime.dashboard = SimpleNamespace(process=SimpleNamespace(poll=lambda: poll_result))
    runtime.upstream_http_origin = "http://127.0.0.1:1"
    runtime.upstream_token = "ours"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Hermes-Session-Token"] == "ours"
        return httpx.Response(status_code)

    runtime.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return runtime


def test_healthy_child_is_left_alone():
    assert asyncio.run(_runtime(None, 200)._dashboard_needs_respawn()) is False


def test_exited_child_is_respawned():
    assert asyncio.run(_runtime(0, 200)._dashboard_needs_respawn()) is True


def test_rejected_token_triggers_respawn():
    assert asyncio.run(_runtime(None, 401)._dashboard_needs_respawn()) is True
