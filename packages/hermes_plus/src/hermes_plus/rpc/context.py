"""Per-process RPC context: the plus.db store, repositories, and an optional upstream caller."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Protocol

from hermes_plus.adapter import GatewayClient, RpcError
from hermes_plus.rpc.errors import FEATURE_UNAVAILABLE, RpcFault
from hermes_plus.store import (
    ApprovalJournal,
    BotIdentities,
    Mailbox,
    Onboarding,
    RoutineMetas,
    SetupProfiles,
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


class GatewayUpstream:
    """Adapts a connected GatewayClient to UpstreamCaller, turning its remote errors into UpstreamRpcError."""

    def __init__(self, client: GatewayClient, *, timeout: float = 120) -> None:
        self._client = client
        self._timeout = timeout

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._client.call(method, params, timeout=self._timeout)
        except RpcError as err:
            raise UpstreamRpcError(err.code, err.message, err.data) from err


@dataclass
class RpcContext:
    store: Store
    upstream: UpstreamCaller | None = None
    internal: UpstreamCaller | None = None
    profile: str | None = None
    capabilities: Any = None
    emit: Any = None

    @cached_property
    def identities(self) -> BotIdentities:
        return BotIdentities(self.store)

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

    @cached_property
    def routines(self) -> RoutineMetas:
        return RoutineMetas(self.store)

    @cached_property
    def setup_profiles(self) -> SetupProfiles:
        return SetupProfiles(self.store)


def require_upstream(ctx: RpcContext, capability: str, extra: dict[str, Any] | None = None) -> UpstreamCaller:
    if ctx.upstream is None:
        raise RpcFault(FEATURE_UNAVAILABLE, f"{capability} needs the Hermes gateway, which is not connected",
                       data={"capability": capability, **(extra or {})})
    return ctx.upstream


def require_internal(ctx: RpcContext, capability: str) -> UpstreamCaller:
    if ctx.internal is None:
        raise RpcFault(FEATURE_UNAVAILABLE, f"{capability} needs the Hermes gateway, which is not connected",
                       data={"capability": capability})
    return ctx.internal
