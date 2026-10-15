"""Per-process RPC context: the plus.db store, repositories, and an optional upstream caller."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Protocol

from hermes_plus.rpc.errors import FEATURE_UNAVAILABLE, RpcFault
from hermes_plus.store import (
    ApprovalJournal,
    BotIdentities,
    Mailbox,
    MemoryNotes,
    Onboarding,
    Store,
    TeamStore,
)


class UpstreamRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class UpstreamCaller(Protocol):
    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class RpcContext:
    store: Store
    upstream: UpstreamCaller | None = None
    profile: str | None = None
    capabilities: Any = None
    emit: Any = None
    internal: Any = None

    @cached_property
    def identities(self) -> BotIdentities:
        return BotIdentities(self.store)

    @cached_property
    def memory(self) -> MemoryNotes:
        return MemoryNotes(self.store)

    @cached_property
    def onboarding(self) -> Onboarding:
        return Onboarding(self.store)

    @cached_property
    def approvals(self) -> ApprovalJournal:
        return ApprovalJournal(self.store)

    @cached_property
    def mailbox(self) -> Mailbox:
        return Mailbox(self.store)

    @cached_property
    def teams(self) -> TeamStore:
        return TeamStore(self.store)


def require_upstream(ctx: RpcContext, capability: str, extra: dict[str, Any] | None = None) -> UpstreamCaller:
    if ctx.upstream is None:
        raise RpcFault(FEATURE_UNAVAILABLE, f"{capability} needs the Hermes gateway, which is not connected",
                       data={"capability": capability, **(extra or {})})
    return ctx.upstream
