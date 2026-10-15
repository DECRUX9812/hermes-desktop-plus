"""fork.onboarding.*: first-run intro flag, failed-start counter, and the setup profile and session it hands off to."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from hermes_plus.adapter import hermes_home, profiles
from hermes_plus.rpc import setup_profile
from hermes_plus.rpc.context import RpcContext, UpstreamRpcError, require_internal
from hermes_plus.rpc.errors import SETUP_PROFILE_FAILED, SETUP_RESET_FAILED, SETUP_SESSION_FAILED, RpcFault
from hermes_plus.rpc.registry import method
from hermes_plus.rpc.setup_profile import SetupProfile
from hermes_plus.store import OnboardingState

GUEST_ONBOARDING_ENV = "HERMES_GUEST_ONBOARDING"

# Setup-profile mutations run on the RPC pool; two overlapping kickoffs must not both create one.
_setup_lock = threading.Lock()


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


@method("fork.onboarding.ensure_setup_profile")
def ensure_setup_profile(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    setup = _ensure_profile(ctx)
    return _profile_wire(setup)


@method("fork.onboarding.ensure_setup_session")
async def ensure_setup_session(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    caller = require_internal(ctx, "fork.onboarding.ensure_setup_session")
    setup = await asyncio.to_thread(_ensure_profile, ctx)
    try:
        listing = await caller.call("session.list", {"profile": setup.name, "title": setup_profile.CHAT_TITLE})
        rows = listing.get("sessions") or []
        if rows:
            row = rows[0]
            session_id = str(row.get("resolved_id") or row["id"])
            empty = not row.get("message_count")
        else:
            created = await caller.call("session.create", {"profile": setup.name})
            await caller.call("session.title", {"session_id": created["session_id"], "title": setup_profile.CHAT_TITLE})
            await caller.call("session.close", {"session_id": created["session_id"]})
            session_id = str(created["stored_session_id"])
            empty = True
    except UpstreamRpcError as err:
        raise RpcFault(SETUP_SESSION_FAILED, err.message) from err
    return {"profile": setup.name, "session_id": session_id, "empty": empty}


@method("fork.onboarding.reset_setup_profile")
def reset_setup_profile(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    with _setup_lock:
        try:
            name = setup_profile.reset(ctx)
        except profiles.ProfileError as err:
            raise RpcFault(SETUP_RESET_FAILED, str(err)) from err
    return {"name": name, "path": str(hermes_home(name)), "reset": True}


def _ensure_profile(ctx: RpcContext) -> SetupProfile:
    with _setup_lock:
        try:
            return setup_profile.ensure(ctx)
        except profiles.ProfileError as err:
            raise RpcFault(SETUP_PROFILE_FAILED, str(err)) from err


def _profile_wire(setup: SetupProfile) -> dict[str, Any]:
    return {"name": setup.name, "path": str(hermes_home(setup.name)), "created": setup.created}
