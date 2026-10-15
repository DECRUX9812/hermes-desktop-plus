from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from hermes_plus.rpc import RpcContext, handle_text
from hermes_plus.store import Store, open_store
from hermes_resolve import resolve_stock_hermes

Call = Callable[..., Awaitable[Any]]


def pytest_report_header(config: pytest.Config) -> str:
    """Name the Hermes binary this session will test, once, at the top of the run."""
    binary, source = resolve_stock_hermes()
    usable = binary.is_file() and os.access(binary, os.X_OK)
    note = "" if usable else "  (not executable — Hermes tests will skip)"
    return f"stock Hermes under test: {binary} {source}{note}"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = open_store(tmp_path / "plus-home")
    yield opened
    opened.close()


@pytest.fixture
def ctx(store: Store) -> RpcContext:
    return RpcContext(store)


@pytest.fixture
def call(ctx: RpcContext) -> Call:
    next_id = 0

    async def send(method: str, params: Any = None) -> Any:
        nonlocal next_id
        next_id += 1
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": next_id, "method": method}
        if params is not None:
            request["params"] = params
        return json.loads(await handle_text(ctx, json.dumps(request)))

    return send
