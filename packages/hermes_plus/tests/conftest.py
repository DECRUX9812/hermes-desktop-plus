from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from hermes_plus.rpc import RpcContext, handle_text
from hermes_plus.store import Store, open_store

Call = Callable[..., Awaitable[Any]]


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
