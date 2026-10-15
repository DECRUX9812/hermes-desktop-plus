from __future__ import annotations

from support import ok

from hermes_plus.rpc.handlers.fork_onboarding import GUEST_ONBOARDING_ENV


async def test_state_is_fresh_and_profile_is_null(call):
    state = ok(await call("fork.onboarding.state"))
    assert state["intro"] == "unseen"
    assert state["failed_starts"] == 0
    assert state["completed_at"] is None
    assert state["profile"] is None


async def test_eligibility_follows_the_guest_environment_flag(call, monkeypatch):
    monkeypatch.delenv(GUEST_ONBOARDING_ENV, raising=False)
    assert ok(await call("fork.onboarding.state"))["eligible"] is False
    monkeypatch.setenv(GUEST_ONBOARDING_ENV, "1")
    assert ok(await call("fork.onboarding.state"))["eligible"] is True


async def test_mark_seen_persists(call):
    assert ok(await call("fork.onboarding.mark_seen"))["intro"] == "seen"
    assert ok(await call("fork.onboarding.state"))["intro"] == "seen"


async def test_failed_starts_count_up_to_the_cap(call):
    for _ in range(3):
        last = ok(await call("fork.onboarding.record_failed_start"))
    assert last["failed_starts"] == 3
    assert last["intro"] == "seen"
