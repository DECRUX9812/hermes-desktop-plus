"""fork.onboarding.{state,mark_seen,record_failed_start}: first-run intro flag and failed-start counter.

The setup-profile lifecycle (ensure_setup_*, reset_setup_profile) is not part of this surface.
"""

from __future__ import annotations

import os
from typing import Any

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.registry import method
from hermes_plus.store import OnboardingState

GUEST_ONBOARDING_ENV = "HERMES_GUEST_ONBOARDING"


def _wire(state: OnboardingState) -> dict[str, Any]:
    eligible = os.environ.get(GUEST_ONBOARDING_ENV) == "1"
    return state.to_wire(eligible=eligible, profile=None)


@method("fork.onboarding.state")
def state(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return _wire(ctx.onboarding.state())


@method("fork.onboarding.mark_seen")
def mark_seen(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return _wire(ctx.onboarding.mark_seen())


@method("fork.onboarding.record_failed_start")
def record_failed_start(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return _wire(ctx.onboarding.record_failed_start())
